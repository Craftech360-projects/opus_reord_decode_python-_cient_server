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
        if not self.deepgram_api_key:
            print("⚠️ DEEPGRAM_API_KEY environment variable not set. Transcription will be disabled.")

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

                                    # Playback
                                    print(f"\n🔊 STARTING PLAYBACK...")
                                    try:
                                        np_audio = np.frombuffer(pcm_data, dtype=np.int16)
                                        print(f"   Audio array shape: {np_audio.shape}")
                                        print(f"   Audio range: {np_audio.min()} to {np_audio.max()}")
                                        
                                        sd.play(np_audio, samplerate=sample_rate)
                                        print(f"   ✅ Playback started successfully")
                                        sd.wait()
                                        print(f"   ✅ Playback completed")
                                    except Exception as e:
                                        print(f"   ❌ Playback failed: {e}")

                                    # Save as WAV
                                    print(f"\n💾 SAVING WAV FILE...")
                                    try:
                                        filename = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{session_id}.wav"
                                        with wave.open(filename, 'wb') as wav_file:
                                            wav_file.setnchannels(channels)
                                            wav_file.setsampwidth(2)  # 16-bit
                                            wav_file.setframerate(sample_rate)
                                            wav_file.writeframes(pcm_data)
                                        print(f"   ✅ WAV file saved: {filename}")
                                    except Exception as e:
                                        print(f"   ❌ WAV save failed: {e}")
                                else:
                                    print(f"\n⚠️ NO PCM DATA - Skipping playback and save")

                            except Exception as e:
                                print(f"❌ Error processing session {session_id}: {e}")
                                import traceback
                                traceback.print_exc()

                        await websocket.send_json({'status': 'session_ended'})
                        print(f"✅ SESSION ENDED - ID: {session_id}\n" + "="*50)

                # Binary (OPUS frame)
                elif 'bytes' in message and session_id:
                    frame_data = message['bytes']
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
    uvicorn.run("server:app", host="0.0.0.0", port=5000, reload=True)