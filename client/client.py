import asyncio
import websockets
import json
import uuid
import pyaudio
import opuslib
import struct
import keyboard  # For keyboard controls
import threading
import time

# Audio configuration (matches protocol spec)
SAMPLE_RATE = 16000  # Hz
CHANNELS = 1
FRAME_DURATION = 20  # ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION / 1000)

class AudioClient:
    def __init__(self, server_uri="ws://localhost:5000/ws/audio"):
        self.server_uri = server_uri
        self.session_id = None
        try:
            self.encoder = opuslib.Encoder(SAMPLE_RATE, CHANNELS, 'voip')
        except opuslib.OpusError as e:
            print(f"Opus encoder initialization failed: {e}")
            raise
        try:
            self.audio = pyaudio.PyAudio()
        except Exception as e:
            print(f"PyAudio initialization failed: {e}")
            raise
        self.stream = None
        self.streaming_active = False  # Controls audio streaming state
        self.space_pressed = False  # Flag for space key press
        
    async def connect(self):
        # Add Origin header for CORS compatibility
        try:
            self.websocket = await websockets.connect(
                self.server_uri,
                extra_headers={"Origin": "http://localhost"}
            )
        except websockets.exceptions.InvalidHandshake as e:
            # Handle potential SSL errors in development
            if "SSL" in str(e) or "TLS" in str(e):
                print(f"SSL error occurred: {e}. Attempting to bypass for development...")
                import ssl
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                self.websocket = await websockets.connect(
                    self.server_uri,
                    extra_headers={"Origin": "http://localhost"},
                    ssl=ssl_context
                )
            else:
                raise
        
    def start_capture(self):
        """Start capturing audio from microphone"""
        try:
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=FRAME_SIZE
            )
        except Exception as e:
            print(f"Microphone access failed: {e}")
            raise
            
    async def start_session(self):
        """Start a new audio session"""
        self.session_id = str(uuid.uuid4())
        await self.websocket.send(json.dumps({
            'type': 'start',
            'session_id': self.session_id,
            'sample_rate': SAMPLE_RATE,
            'channels': CHANNELS,
            'frame_duration': FRAME_DURATION
        }))
        response = await self.websocket.recv()
        print("Server response:", response)
        
    async def stop_session(self):
        """Stop the current audio session"""
        if self.session_id:
            await self.websocket.send(json.dumps({
                'type': 'stop',
                'session_id': self.session_id
            }))
            self.session_id = None

    async def handle_space_press(self):
        """Handle space key press to toggle streaming"""
        self.streaming_active = not self.streaming_active
        if self.streaming_active:
            await self.start_session()
            print("\nAudio streaming STARTED")
        else:
            await self.stop_session()
            print("\nAudio streaming STOPPED")
        
    async def stream_audio(self):
        """Capture and stream audio to server when active"""
        try:
            while True:
                # Check for space key press
                if self.space_pressed:
                    self.space_pressed = False
                    await self.handle_space_press()
                
                if self.streaming_active:
                    try:
                        # Use non-blocking read with timeout
                        if self.stream.get_read_available() >= FRAME_SIZE:
                            # Read audio data
                            raw_frame = self.stream.read(FRAME_SIZE, exception_on_overflow=False)
                            
                            try:
                                # Encode to Opus
                                opus_frame = self.encoder.encode(raw_frame, FRAME_SIZE)
                            except opuslib.OpusError as e:
                                print(f"Opus encoding error: {e}")
                                continue
                            
                            # Create message: [4-byte length][Opus frame]
                            frame_length = len(opus_frame)
                            message = struct.pack('>I', frame_length) + opus_frame
                            
                            try:
                                # Send to server
                                await self.websocket.send(message)
                            except websockets.exceptions.ConnectionClosed:
                                print("WebSocket connection closed unexpectedly")
                                break
                            except Exception as e:
                                print(f"Error sending audio: {e}")
                                break
                        else:
                            # Short sleep if no audio data available
                            await asyncio.sleep(0.01)
                            
                    except Exception as e:
                        print(f"Streaming error: {e}")
                        break
                else:
                    # Avoid high CPU usage while inactive
                    await asyncio.sleep(0.1)
                    
        except Exception as e:
            print(f"Fatal streaming error: {e}")
            
    async def stop(self):
        """Stop streaming and close connection"""
        await self.stop_session()
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.audio.terminate()
        await self.websocket.close()

async def main():
    client = AudioClient()
    await client.connect()
    client.start_capture()
    
    # Keyboard control setup
    print("Press SPACE to start/stop audio streaming. Press Ctrl+C to exit.")
    
    # Keyboard event handler (runs in separate thread)
    def on_key_event(e):
        if e.event_type == keyboard.KEY_DOWN and e.name == 'space':
            client.space_pressed = True
    
    # Register the keyboard handler
    keyboard.hook(on_key_event)
    
    try:
        await client.stream_audio()
    except KeyboardInterrupt:
        print("Stopping...")
        await client.stop()
    finally:
        keyboard.unhook_all()

if __name__ == "__main__":
    asyncio.run(main())