from fastapi import FastAPI  
import uvicorn  
  
app = FastAPI(title="Trading Bot API", version="1.0.0")  
  
@app.get("/api/test")  
def test_endpoint():  
    return {"ok": True}  
  
if __name__ == "__main__":  
    uvicorn.run(app, host="127.0.0.1", port=8000) 
