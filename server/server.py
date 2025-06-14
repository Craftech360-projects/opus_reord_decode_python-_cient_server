import json
import uuid
from datetime import datetime

import numpy as np
import opuslib
import sounddevice as sd
import wave

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

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
                
                print(f"Received message type: {list(message.keys())}")  # Debug message type

                # Handle disconnect message
                if message.get('type') == 'websocket.disconnect':
                    print(f"🔌 WebSocket disconnect message received")
                    break

                # Text (JSON)
                if 'text' in message:
                    print(f"Text message: {message['text']}")  # Debug text content
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
                            'decode_error_count': 0
                        }

                        print(f"🎤 SESSION STARTED - ID: {session_id}")
                        print(f"   Sample Rate: {sample_rate}Hz, Channels: {channels}, Frame Duration: {frame_duration}ms")

                        await websocket.send_json({
                            'type': 'ack',
                            'session_id': session_id,
                            'status': 'session_started'
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

                                # Initialize decoder
                                try:
                                    decoder = opuslib.Decoder(sample_rate, channels)
                                    print(f"✅ Opus decoder initialized successfully")
                                except Exception as e:
                                    print(f"❌ Opus decoder initialization failed: {e}")
                                    raise

                                # Decode all frames
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

                                if successful_frames > 0:
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
                                    print(f"\n⚠️ NO SUCCESSFUL FRAMES - Skipping playback and save")

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

        except WebSocketDisconnect:
            if session_id and session_id in self.active_sessions:
                session_info = self.active_sessions[session_id]
                print(f"\n🔌 WebSocket disconnected - Session {session_id}")
                print(f"   Frames received before disconnect: {session_info.get('frame_count', 0)}")
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



# This code implements a FastAPI WebSocket server that handles audio streaming sessions.
# It supports starting and stopping sessions, receiving Opus-encoded audio frames,
# decoding them, and optionally playing back the audio and saving it as a WAV file.
# The server maintains active sessions, decodes received frames using Opus,
# and provides detailed logging of the process, including error handling for decoding issues.
# The server also supports CORS to allow cross-origin requests.
# The server can be run with Uvicorn, listening on port 5000.
# The server is designed to handle multiple concurrent WebSocket connections,
# allowing multiple clients to stream audio simultaneously.
# The server uses the opuslib library for Opus encoding/decoding,
# sounddevice for audio playback, and wave for saving audio files in WAV format.
# The server is structured to handle WebSocket messages efficiently,
# processing both text messages (for session control) and binary messages (for audio frames).
# The server also includes detailed logging for debugging and monitoring purposes,
# providing insights into the audio streaming process, including session management,
# frame handling, and playback operations.

#needd to implemet deepgram transcription