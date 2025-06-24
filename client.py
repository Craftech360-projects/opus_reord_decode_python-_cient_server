import asyncio
import websockets
import json
import pyaudio
import opuslib
import struct
import keyboard
import threading
import queue

SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_DURATION = 40  # Updated from 20 to 40 ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION / 1000)

class AudioClient:
    def __init__(self, server_uri="ws://192.168.1.3:5000/ws/audio"):
        self.server_uri = server_uri
        self.decoder = opuslib.Decoder(SAMPLE_RATE, CHANNELS)
        self.audio = pyaudio.PyAudio()
        self.output_stream = None
        self.audio_queue = queue.Queue()
        self.playback_thread = None
        self.playback_active = False
        self.space_pressed = False
        self.frame_duration = FRAME_DURATION  # Default
        self.frame_size = FRAME_SIZE          # Default

    async def connect(self):
        self.websocket = await websockets.connect(
            self.server_uri,
            extra_headers={"Origin": "http://localhost"}
        )

    def start_playback(self):
        self.output_stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            output=True,
            frames_per_buffer=FRAME_SIZE
        )
        self.playback_active = True
        self.playback_thread = threading.Thread(target=self.playback_worker, daemon=True)
        self.playback_thread.start()

    def playback_worker(self):
        while self.playback_active:
            try:
                audio_data = self.audio_queue.get(timeout=0.1)
                if audio_data is None:
                    break
                self.output_stream.write(audio_data)
            except queue.Empty:
                continue

    def queue_audio_for_playback(self, pcm_data):
        self.audio_queue.put(pcm_data)

    async def handle_server_messages(self):
        try:
            while True:
                message = await self.websocket.recv()

                if isinstance(message, str):
                    try:
                        data = json.loads(message)
                        if data.get('type') == 'ack':
                            print("✅ Session acknowledged by server")
                        elif data.get('type') == 'audio_response':
                            print("🎧 Audio Stream Details:")
                            print(f" - Format: {data.get('format')}")
                            print(f" - Sample Rate: {data.get('sample_rate')} Hz")
                            print(f" - Channels: {data.get('channels')}")
                            print(f" - Frame Duration: {data.get('frame_duration')} ms")
                            print(f" - Total Frames: {data.get('frame_count')}")
                            # Update frame_duration and frame_size based on server info
                            self.frame_duration = data.get('frame_duration', FRAME_DURATION)
                            self.frame_size = int(SAMPLE_RATE * self.frame_duration / 1000)
                            if not self.output_stream:
                                self.start_playback()
                    except json.JSONDecodeError:
                        print("❌ Invalid JSON received from server.")
                elif isinstance(message, bytes):
                    if len(message) >= 4:
                        frame_length = struct.unpack('>I', message[:4])[0]
                        if len(message) == frame_length + 4:
                            opus_frame = message[4:]
                            # Use dynamic frame_size for decoding
                            pcm_data = self.decoder.decode(opus_frame, self.frame_size)
                            self.queue_audio_for_playback(pcm_data)
        except Exception as e:
            print(f"❌ Error receiving message: {e}")

    async def start_session(self):
        await self.websocket.send(json.dumps({
            'message': 'start'
        }))
        print(f"📤 Sent: {{ 'message': 'start' }}")

    async def stop(self):
        self.playback_active = False
        self.audio_queue.put(None)
        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()
        self.audio.terminate()
        await self.websocket.close()

async def main():
    client = AudioClient()
    await client.connect()
    message_handler_task = asyncio.create_task(client.handle_server_messages())

    def on_key_event(e):
        if e.event_type == keyboard.KEY_DOWN and e.name == 'space':
            client.space_pressed = True

    keyboard.hook(on_key_event)

    try:
        while True:
            if client.space_pressed:
                client.space_pressed = False
                await client.start_session()
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        print("🛑 Exiting...")
    finally:
        await client.stop()
        message_handler_task.cancel()
        keyboard.unhook_all()

if __name__ == "__main__":
    asyncio.run(main())
