import asyncio
import websockets
import json
import subprocess
import time

async def test_websocket():
    try:
        uri = "ws://127.0.0.1:8001/ws"
        print(f"Connecting to {uri}...")
        async with websockets.connect(uri) as websocket:
            print("WebSocket connected successfully")

            # Send a test message
            test_message = {"type": "test", "data": "hello"}
            await websocket.send(json.dumps(test_message))
            print(f"Sent: {test_message}")

            # Try to receive a response
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                print(f"Received: {response}")
            except asyncio.TimeoutError:
                print("No response received (expected for echo)")

            await websocket.close()
            print("WebSocket test completed")

    except Exception as e:
        print(f"WebSocket error: {e}")

def start_server():
    """Start the server in background"""
    print('Starting server...')
    return subprocess.Popen(['python', '-c', 'import uvicorn; uvicorn.run("trading_bot_v2.api_server:app", host="127.0.0.1", port=8001)'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def stop_server(proc):
    """Stop the server"""
    if proc:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    # Start server
    proc = start_server()
    time.sleep(5)  # Wait for startup

    try:
        asyncio.run(test_websocket())
    finally:
        stop_server(proc)