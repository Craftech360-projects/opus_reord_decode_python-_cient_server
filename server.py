import json
import uuid
import asyncio
import struct
import os
from datetime import datetime

import websockets
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi import WebSocketDisconnect

from pydub import AudioSegment
import opuslib
from mutagen.mp3 import MP3

app = FastAPI()

# CORS settings
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

    def generate_opus_from_mp3(self, mp3_path, sample_rate=16000, channels=1, frame_duration=40):
        # Read encoded MP3 properties
        try:
            mp3_info = MP3(mp3_path).info
            encoded_bitrate = mp3_info.bitrate / 1000  # kbps
            encoded_sample_rate = mp3_info.sample_rate
            encoded_channels = mp3_info.channels
            encoded_duration = mp3_info.length
        except Exception as e:
            print(f"⚠️ Failed to read MP3 metadata with mutagen: {e}")
            encoded_bitrate = encoded_sample_rate = encoded_channels = encoded_duration = None

        audio_segment = AudioSegment.from_mp3(mp3_path)

        # Print both encoded and PCM stats
        print("📊 Original MP3 Properties:")
        print(f" - Frame Rate (Hz): {audio_segment.frame_rate}")
        print(f" - Channels: {audio_segment.channels}")
        print(f" - Sample Width (bytes): {audio_segment.sample_width}")
        print(f" - Frame Width: {audio_segment.frame_width}")
        print(f" - Frame Count: {audio_segment.frame_count()}")
        print(f" - Duration (sec): {audio_segment.duration_seconds}")
        if encoded_bitrate:
            print(f" - Encoded MP3 Bitrate: {encoded_bitrate:.2f} kbps")
            print(f" - MP3 Sample Rate: {encoded_sample_rate} Hz")
            print(f" - MP3 Channels: {encoded_channels}")
            print(f" - MP3 Duration: {encoded_duration:.2f} sec")
        else:
            bitrate_kbps = (len(audio_segment.raw_data) * 8) / (1000 * audio_segment.duration_seconds)
            print(f" - Approx Bitrate (PCM): {bitrate_kbps:.2f} kbps")

        # Downsample and prepare
        audio_segment = audio_segment.set_frame_rate(sample_rate)
        audio_segment = audio_segment.set_channels(channels)
        audio_segment = audio_segment.set_sample_width(2)

        pcm_data = audio_segment.raw_data
        encoder = opuslib.Encoder(sample_rate, channels, 'voip')
        frame_size = (frame_duration * sample_rate) // 1000
        frame_bytes = frame_size * channels * 2

        opus_frames = []
        for i in range(0, len(pcm_data), frame_bytes):
            pcm_frame = pcm_data[i:i+frame_bytes]
            if len(pcm_frame) < frame_bytes:
                pcm_frame += b'\x00' * (frame_bytes - len(pcm_frame))
            opus_frame = encoder.encode(pcm_frame, frame_size)
            framed = struct.pack('>I', len(opus_frame)) + opus_frame
            opus_frames.append(framed)

        return opus_frames

    async def handle_websocket(self, websocket: WebSocket):
        await websocket.accept()
        session_id = None

        try:
            while True:
                try:
                    message = await websocket.receive()
                except WebSocketDisconnect:
                    print("🔌 WebSocket disconnected")
                    break

                if 'text' in message:
                    data = json.loads(message['text'])
                    if data.get('message') == 'start':
                        session_id = str(uuid.uuid4())
                        sample_rate = 16000
                        channels = 1
                        frame_duration = 60  

                        print(f"🎤 Session started: {session_id}")

                        # Send ACK
                        await websocket.send_json({
                            'type': 'ack',
                            'session_id': session_id,
                            'status': 'session_started',
                            'transcription_enabled': False
                        })

                        # Generate Opus from default.mp3
                        mp3_path = "default.mp3"
                        if not os.path.exists(mp3_path):
                            await websocket.send_json({
                                'type': 'error',
                                'message': 'default.mp3 not found on server.'
                            })
                            return

                        opus_frames = self.generate_opus_from_mp3(mp3_path, sample_rate, channels, frame_duration)

                        # Send audio metadata
                        await websocket.send_json({
                            'type': 'audio_response',
                            'format': 'opus',
                            'sample_rate': sample_rate,
                            'channels': channels,
                            'frame_duration': frame_duration,
                            'frame_count': len(opus_frames),
                            'text': 'default.mp3 audio stream'
                        })

                        # Stream the Opus frames
                        for frame in opus_frames:
                            await websocket.send_bytes(frame)
                            await asyncio.sleep(frame_duration / 1000.0)

                        # Send stop signal
                        await websocket.send_json({
                            'type': 'stop',
                            'session_id': session_id
                        })

        except Exception as e:
            print(f"❌ Error in WebSocket handler: {e}")

# Server instance
audio_server = AudioServer()

@app.websocket("/ws/audio")
async def websocket_endpoint(websocket: WebSocket):
    await audio_server.handle_websocket(websocket)

# Check for default.mp3 presence before starting the server
if not os.path.exists("default.mp3"):
    print("❌ ERROR: default.mp3 not found in the server directory!")
else:
    print("✅ default.mp3 found.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=5000, reload=True)
