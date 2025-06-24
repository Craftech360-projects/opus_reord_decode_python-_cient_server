import json
import uuid
from datetime import datetime
import os
import asyncio
import websockets

import numpy as np
import opuslib
import sounddevice as sd
import wave
from dotenv import load_dotenv
import requests  # For Groq API calls
import base64    # For audio encoding
from pydub import AudioSegment
from pydub.playback import play
import io
import struct

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Load environment variables from .env file
load_dotenv()

class DeepgramStreamer:
    def __init__(self, sample_rate, channels, api_key):
        self.sample_rate = sample_rate
        self.channels = channels
        self.api_key = api_key
        self.connection = None
        self.transcript = ""
        self.receive_task = None
        self.is_final_received = False
        self.final_transcript = ""

    async def start(self):
        try:
            # Enhanced URL with more specific parameters
            url = (
                f"wss://api.deepgram.com/v1/listen?"
                f"encoding=linear16&"
                f"sample_rate={self.sample_rate}&"
                f"channels={self.channels}&"
                f"punctuate=true&"
                f"interim_results=true&"
                f"endpointing=300&"
                f"vad_events=true"
            )
            
            self.connection = await websockets.connect(
                url,
                extra_headers={"Authorization": f"Token {self.api_key}"}
            )
            self.receive_task = asyncio.create_task(self.receive_loop())
            print("   ✅ Deepgram connection established")
        except Exception as e:
            print(f"   ❌ Deepgram connection failed: {e}")
            raise

    async def receive_loop(self):
        try:
            async for message in self.connection:
                try:
                    data = json.loads(message)
                    print(f"   🎤 Deepgram response: {json.dumps(data, indent=2)}")
                    
                    # Handle different types of responses
                    if data.get('type') == 'Results':
                        # This is the main transcription result
                        channel = data.get('channel', {})
                        alternatives = channel.get('alternatives', [])
                        
                        if alternatives:
                            transcript = alternatives[0].get('transcript', '')
                            is_final = data.get('is_final', False)
                            
                            if transcript.strip():
                                print(f"   📝 Transcript ({'final' if is_final else 'interim'}): '{transcript}'")
                                
                                if is_final:
                                    self.final_transcript += transcript + " "
                                    self.is_final_received = True
                                else:
                                    # Store interim results too
                                    self.transcript = transcript
                    
                    elif data.get('type') == 'Metadata':
                        print(f"   📊 Deepgram metadata: {data}")
                    
                    elif data.get('type') == 'SpeechStarted':
                        print(f"   🗣️ Speech started detected")
                    
                    elif data.get('type') == 'UtteranceEnd':
                        print(f"   🔚 Utterance end detected")
                        
                except json.JSONDecodeError as e:
                    print(f"   ❌ JSON decode error: {e}")
                except Exception as e:
                    print(f"   ❌ Error processing Deepgram message: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            print("   ❌ Deepgram connection closed")
        except Exception as e:
            print(f"   ❌ Error in Deepgram receive_loop: {e}")

    async def send_audio(self, pcm_data):
        if self.connection and not self.connection.closed:
            try:
                await self.connection.send(pcm_data)
                print(f"   📤 Sent {len(pcm_data)} bytes to Deepgram")
            except Exception as e:
                print(f"   ❌ Error sending audio to Deepgram: {e}")
        else:
            print("   ❌ Deepgram connection is not open")

    async def finalize(self):
        """Send final message and wait for final results"""
        try:
            if self.connection and not self.connection.closed:
                # Send close frame to signal end of audio
                print("   🔚 Sending close frame to Deepgram...")
                await self.connection.send(json.dumps({"type": "CloseStream"}))
                
                # Wait a bit for final results
                await asyncio.sleep(1)
                
                # Close connection
                await self.connection.close()
                
            if self.receive_task and not self.receive_task.done():
                self.receive_task.cancel()
                try:
                    await self.receive_task
                except asyncio.CancelledError:
                    pass
                    
        except Exception as e:
            print(f"   ❌ Error finalizing Deepgram: {e}")

    def get_final_transcript(self):
        """Get the complete final transcript"""
        return self.final_transcript.strip() or self.transcript.strip()

app = FastAPI()

# Add CORS middleware to allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AudioServer:
    def __init__(self):
        self.active_sessions = {}
        self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        
        if not self.deepgram_api_key:
            print("⚠️ DEEPGRAM_API_KEY environment variable not set. Transcription will be disabled.")
        if not self.groq_api_key:
            print("⚠️ GROQ_API_KEY environment variable not set. LLM responses will be disabled.")
        if not self.elevenlabs_api_key:
            print("⚠️ ELEVENLABS_API_KEY environment variable not set. Audio generation will be disabled.")

    def get_llm_response(self, transcript):
        """Get response from Groq Llama 3.3"""
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.groq_api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "llama3-70b-8192",
            "messages": [{"role": "user", "content": transcript + "\nRespond in under 50 words."}],
            "temperature": 0.7
        }
        
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    
    def generate_audio_opus(self, text, target_sample_rate=16000, target_channels=1, frame_duration=60):
        """Generate Opus audio from text using ElevenLabs and convert to Opus format"""
        if not self.elevenlabs_api_key:
            print("   ⚠️ ElevenLabs API key not available, skipping audio generation")
            return []
            
        url = "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"  # Default voice ID
        headers = {
            "xi-api-key": self.elevenlabs_api_key,
            "Content-Type": "application/json"
        }
        data = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            
            # ElevenLabs returns MP3 audio, convert to PCM first
            print(f"   🎵 Received {len(response.content)} bytes of MP3 audio from ElevenLabs")
            
            # Load MP3 audio using pydub
            audio_segment = AudioSegment.from_mp3(io.BytesIO(response.content))
            print(f"   📊 Original audio: {audio_segment.frame_rate}Hz, {audio_segment.channels} channels, {len(audio_segment)}ms duration")
            
            # Convert to target format (16kHz mono)
            if audio_segment.frame_rate != target_sample_rate:
                audio_segment = audio_segment.set_frame_rate(target_sample_rate)
                print(f"   🔄 Resampled to {target_sample_rate}Hz")
                
            if audio_segment.channels != target_channels:
                audio_segment = audio_segment.set_channels(target_channels)
                print(f"   🔄 Converted to {target_channels} channel(s)")
            
            # Ensure 16-bit PCM
            audio_segment = audio_segment.set_sample_width(2)  # 2 bytes = 16 bits
            
            # Get raw PCM data
            pcm_data = audio_segment.raw_data
            print(f"   ✅ Generated PCM audio: {len(pcm_data)} bytes, {target_sample_rate}Hz, {target_channels} channel(s), 16-bit")
            
            # Convert PCM to Opus frames
            try:
                # Initialize Opus encoder
                encoder = opuslib.Encoder(target_sample_rate, target_channels, 'voip')
                print(f"   🎵 Opus encoder initialized")
                
                # Calculate frame parameters
                frame_size = (frame_duration * target_sample_rate) // 1000
                frame_bytes = frame_size * target_channels * 2  # 16-bit = 2 bytes per sample
                
                print(f"   📏 Frame size: {frame_size} samples, {frame_bytes} bytes per frame")
                
                opus_frames = []
                
                # Split PCM data into frames and encode each
                for i in range(0, len(pcm_data), frame_bytes):
                    pcm_frame = pcm_data[i:i + frame_bytes]
                    
                    # Pad the last frame if necessary
                    if len(pcm_frame) < frame_bytes:
                        padding = b'\x00' * (frame_bytes - len(pcm_frame))
                        pcm_frame = pcm_frame + padding
                        print(f"   🔧 Padded last frame: {len(pcm_frame)} bytes")
                    
                    try:
                        # Encode PCM frame to Opus
                        opus_frame = encoder.encode(pcm_frame, frame_size)
                        opus_frames.append(opus_frame)
                        
                    except opuslib.OpusError as e:
                        print(f"   ❌ Opus encoding error for frame {len(opus_frames)}: {e}")
                        continue
                
                print(f"   ✅ Generated {len(opus_frames)} Opus frames")
                return opus_frames
                
            except Exception as e:
                print(f"   ❌ Opus conversion failed: {e}")
                return []
            
        except Exception as e:
            print(f"   ❌ Audio generation failed: {e}")
            return []

    async def send_opus_frames(self, websocket, opus_frames, frame_duration=60, session_id=None):
        """Send Opus frames to client with proper framing"""
        try:
            print(f"   📤 Sending {len(opus_frames)} Opus frames to client...")
            
            for i, opus_frame in enumerate(opus_frames):
                # Create message with 4-byte length header + Opus frame
                frame_length = len(opus_frame)
                message = struct.pack('>I', frame_length) + opus_frame
                
                # Send the frame
                await websocket.send_bytes(message)
                print(f"   📦 Sent frame {i+1}/{len(opus_frames)}: {frame_length} bytes")
                
                # Add small delay between frames to simulate real-time playback
                await asyncio.sleep(frame_duration / 1000.0)  # Convert ms to seconds
                
            print(f"   ✅ All Opus frames sent successfully")
            
            if session_id:
                # Send stop signal after audio streaming completes
                stop_message = {
                    "type": "stop",
                    "session_id": session_id
                }
                await websocket.send_json(stop_message)
                print(f"   🛑 Sent stop signal to client")
            else:
                print("   ⚠️ Skipping stop signal - session_id not available")
            
        except Exception as e:
            print(f"   ❌ Error sending Opus frames: {e}")

    async def handle_websocket(self, websocket: WebSocket):
        await websocket.accept()
        session_id = None

        try:
            while True:
                try:
                    message = await websocket.receive()
                except WebSocketDisconnect:
                    print(f"🔌 WebSocket disconnected via exception")
                    break
                
                print(f"Received message type: {list(message.keys())}")

                # Handle disconnect message
                if message.get('type') == 'websocket.disconnect':
                    print(f"🔌 WebSocket disconnect message received")
                    break

                # Text (JSON)
                if 'text' in message:
                    print(f"Text message: {message['text']}")
                    data = json.loads(message['text'])

                    if data['type'] == 'start':
                        session_id = data.get('session_id', str(uuid.uuid4()))
                        sample_rate = data['sample_rate']
                        channels = data['channels']
                        frame_duration = data['frame_duration']

                        self.active_sessions[session_id] = {
                            'sample_rate': sample_rate,
                            'channels': channels,
                            'frame_duration': frame_duration,
                            'frames': [],
                            'frame_count': 0,
                            'decode_success_count': 0,
                            'decode_error_count': 0,
                            'pcm_data': b'',
                            'deepgram': None,
                            'transcript': ""
                        }

                        print(f"🎤 SESSION STARTED - ID: {session_id}")
                        print(f"   Sample Rate: {sample_rate}Hz, Channels: {channels}, Frame Duration: {frame_duration}ms")

                        # Initialize Deepgram if API key is available
                        session_info = self.active_sessions[session_id]
                        if self.deepgram_api_key:
                            try:
                                deepgram = DeepgramStreamer(
                                    sample_rate=sample_rate,
                                    channels=channels,
                                    api_key=self.deepgram_api_key
                                )
                                await deepgram.start()
                                session_info['deepgram'] = deepgram
                                print(f"   🎤 Deepgram transcription enabled")
                            except Exception as e:
                                print(f"   ❌ Failed to initialize Deepgram: {e}")
                        else:
                            print("   ⚠️ Deepgram transcription disabled (no API key)")

                        await websocket.send_json({
                            'type': 'ack',
                            'session_id': session_id,
                            'status': 'session_started',
                            'transcription_enabled': session_info['deepgram'] is not None
                        })

                    elif data['type'] == 'stop' and session_id:
                        session_info = self.active_sessions.pop(session_id, None)
                        if session_info:
                            print(f"\n🛑 SESSION STOPPING - ID: {session_id}")
                            print(f"   Total frames received: {session_info['frame_count']}")
                            print(f"   Frames to decode: {len(session_info['frames'])}")
                            
                            try:
                                frame_duration = session_info['frame_duration']
                                sample_rate = session_info['sample_rate']
                                channels = session_info['channels']
                                frame_size = (frame_duration * sample_rate) // 1000

                                print(f"🔧 OPUS DECODER SETUP:")
                                print(f"   Frame size: {frame_size} samples")
                                print(f"   Expected PCM bytes per frame: {frame_size * channels * 2}")

                                # Handle Deepgram finalization FIRST
                                transcript = ""
                                if session_info['deepgram']:
                                    print(f"   🎤 Finalizing Deepgram transcription...")
                                    await session_info['deepgram'].finalize()
                                    transcript = session_info['deepgram'].get_final_transcript()
                                    
                                    if transcript:
                                        print(f"   ✅ Final transcript: '{transcript}'")
                                        session_info['transcript'] = transcript
                                        
                                        # Save transcript to file
                                        transcript_filename = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{session_id}.txt"
                                        with open(transcript_filename, 'w', encoding='utf-8') as f:
                                            f.write(transcript)
                                        print(f"   ✅ Transcript saved: {transcript_filename}")
                                        
                                        # Generate LLM response and audio if Groq API key is available
                                        if self.groq_api_key and transcript:
                                            try:
                                                # Get LLM response from Groq
                                                llm_response = self.get_llm_response(transcript)
                                                print(f"   🤖 LLM Response: {llm_response}")
                                                
                                                # Generate Opus audio frames from LLM response
                                                opus_frames = self.generate_audio_opus(
                                                    llm_response, 
                                                    target_sample_rate=sample_rate, 
                                                    target_channels=channels,
                                                    frame_duration=60
                                                )
                                                
                                                # Send audio response to client
                                                if opus_frames:
                                                    print(f"   📤 Sending Opus audio to client: {len(opus_frames)} frames")
                                                    
                                                    # Send audio metadata first
                                                    await websocket.send_json({
                                                        'type': 'audio_response',
                                                        'format': 'opus',
                                                        'sample_rate': sample_rate,
                                                        'channels': channels,
                                                        'frame_duration': frame_duration,
                                                        'frame_count': len(opus_frames),
                                                        'text': llm_response
                                                    })
                                                    
                                                    # Send Opus frames
                                                    await self.send_opus_frames(websocket, opus_frames, frame_duration, session_id)
                                                    print("   🔊 Opus audio response sent to client")
                                                else:
                                                    print("   ⚠️ No Opus frames generated")
                                                    
                                            except Exception as e:
                                                print(f"   ❌ Groq/ElevenLabs processing failed: {e}")
                                                import traceback
                                                traceback.print_exc()
                                    else:
                                        print("   ⚠️ No transcript generated")

                                # Initialize decoder for file processing
                                try:
                                    decoder = opuslib.Decoder(sample_rate, channels)
                                    print(f"✅ Opus decoder initialized successfully")
                                except Exception as e:
                                    print(f"❌ Opus decoder initialization failed: {e}")
                                    raise

                                # Use accumulated PCM data if available (from real-time processing)
                                if session_info.get('pcm_data'):
                                    pcm_data = session_info['pcm_data']
                                    total_pcm_bytes = len(pcm_data)
                                    successful_frames = session_info['frame_count'] - session_info['decode_error_count']
                                    
                                    print(f"\n📊 USING REAL-TIME PCM DATA:")
                                    print(f"   ✅ Total PCM data: {total_pcm_bytes} bytes ({total_pcm_bytes/1024:.1f} KB)")
                                else:
                                    # Fallback: Decode all frames
                                    pcm_data = b''
                                    successful_frames = 0
                                    failed_frames = 0
                                    total_pcm_bytes = 0

                                    print(f"\n🎵 DECODING FRAMES:")
                                    for i, frame in enumerate(session_info['frames']):
                                        try:
                                            print(f"   Frame {i+1}/{len(session_info['frames'])}: {len(frame)} bytes -> ", end="")
                                            pcm_frame = decoder.decode(frame, frame_size)
                                            pcm_data += pcm_frame
                                            successful_frames += 1
                                            total_pcm_bytes += len(pcm_frame)
                                            print(f"✅ {len(pcm_frame)} PCM bytes")
                                            
                                        except opuslib.OpusError as e:
                                            failed_frames += 1
                                            print(f"❌ Opus decode error: {e}")
                                        except Exception as e:
                                            failed_frames += 1
                                            print(f"❌ General decode error: {e}")

                                    print(f"\n📊 DECODING SUMMARY:")
                                    print(f"   ✅ Successful frames: {successful_frames}/{len(session_info['frames'])}")
                                    print(f"   ❌ Failed frames: {failed_frames}/{len(session_info['frames'])}")
                                    print(f"   📏 Total PCM data: {total_pcm_bytes} bytes ({total_pcm_bytes/1024:.1f} KB)")

                                if total_pcm_bytes > 0:
                                    duration_seconds = total_pcm_bytes / (sample_rate * channels * 2)
                                    print(f"   ⏱️ Audio duration: {duration_seconds:.2f} seconds")

                                    # Save as WAV
                                    print(f"\n💾 SAVING WAV FILE...")
                                    try:
                                        output_folder = "output_wav"
                                        os.makedirs(output_folder, exist_ok=True)  # Create folder if it doesn't exist
                                        filename = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{session_id}.wav"
                                        filepath = os.path.join(output_folder, filename)
                                        with wave.open(filepath, 'wb') as wav_file:
                                            wav_file.setnchannels(channels)
                                            wav_file.setsampwidth(2)  # 16-bit
                                            wav_file.setframerate(sample_rate)
                                            wav_file.writeframes(pcm_data)
                                        print(f"   ✅ WAV file saved: {filepath}")
                                    except Exception as e:
                                        print(f"   ❌ WAV save failed: {e}")
                                else:
                                    print(f"\n⚠️ NO PCM DATA - Skipping playback and save")

                            except Exception as e:
                                print(f"❌ Error processing session {session_id}: {e}")
                                import traceback
                                traceback.print_exc()

                        print(f"✅ SESSION ENDED - ID: {session_id}\n" + "="*50)

                # Binary (OPUS frame)
                elif 'bytes' in message and session_id:
                    frame_data = message['bytes']
                    # --- Add this check to prevent KeyError ---
                    if session_id not in self.active_sessions:
                        print(f"❌ Received frame for unknown session_id: {session_id}")
                        await websocket.send_json({
                            'type': 'error',
                            'code': 'unknown_session',
                            'message': f'Session {session_id} not found. Please start a session first.'
                        })
                        continue
                    # --- End of added check ---
                    session_info = self.active_sessions[session_id]
                    session_info['frame_count'] += 1
                    
                    print(f"📦 Frame {session_info['frame_count']}: {len(frame_data)} bytes -> ", end="")
                    
                    if len(frame_data) < 4:
                        print(f"❌ Too short (need at least 4 bytes for length header)")
                        continue
                        
                    frame_length = int.from_bytes(frame_data[:4], 'big')
                    opus_frame = frame_data[4:4+frame_length]

                    if len(opus_frame) != frame_length:
                        print(f"❌ Length mismatch: expected {frame_length}, got {len(opus_frame)}")
                        await websocket.send_json({
                            'type': 'error',
                            'code': 'invalid_frame',
                            'message': f'Expected {frame_length} bytes, got {len(opus_frame)}'
                        })
                    else:
                        print(f"✅ Valid Opus frame ({len(opus_frame)} bytes)")
                        session_info['frames'].append(opus_frame)

                        # Initialize decoder if not already done
                        if 'decoder' not in session_info:
                            try:
                                frame_size = (session_info['frame_duration'] * session_info['sample_rate']) // 1000
                                session_info['decoder'] = opuslib.Decoder(session_info['sample_rate'], session_info['channels'])
                                print(f"   ✅ Opus decoder initialized for real-time processing")
                            except Exception as e:
                                print(f"   ❌ Opus decoder initialization failed: {e}")
                                continue

                        # Decode immediately and send to Deepgram
                        try:
                            frame_size = (session_info['frame_duration'] * session_info['sample_rate']) // 1000
                            pcm_frame = session_info['decoder'].decode(opus_frame, frame_size)
                            session_info['pcm_data'] += pcm_frame
                            session_info['decode_success_count'] += 1
                            
                            # Send to Deepgram for real-time transcription
                            if session_info.get('deepgram'):
                                await session_info['deepgram'].send_audio(pcm_frame)
                                
                        except Exception as e:
                            print(f"   ❌ Real-time decode/send error: {e}")
                            session_info['decode_error_count'] += 1

        except WebSocketDisconnect:
            if session_id and session_id in self.active_sessions:
                session_info = self.active_sessions[session_id]
                print(f"\n🔌 WebSocket disconnected - Session {session_id}")
                print(f"   Frames received before disconnect: {session_info.get('frame_count', 0)}")
                
                # Clean up Deepgram connection
                if session_info.get('deepgram'):
                    try:
                        await session_info['deepgram'].finalize()
                    except Exception as e:
                        print(f"   ❌ Error cleaning up Deepgram: {e}")
                
                del self.active_sessions[session_id]
            else:
                print("🔌 WebSocket disconnected - No active session")


# Instance of server logic
audio_server = AudioServer()


@app.websocket("/ws/audio")
async def websocket_endpoint(websocket: WebSocket):
    await audio_server.handle_websocket(websocket)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=True)