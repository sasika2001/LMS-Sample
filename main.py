from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
import datetime
import pytz
from typing import List

app = FastAPI()

# MongoDB Connection
MONGO_URI = "mongodb+srv://admin:1234@cluster0.ogyx0.mongodb.net/LMS?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "LMS"

db_client = None
db = None

@app.on_event("startup")
async def startup_db_client():
    global db_client, db
    db_client = AsyncIOMotorClient(MONGO_URI)
    db = db_client[DB_NAME]
    print("Connected to MongoDB")

@app.on_event("shutdown")
async def shutdown_db_client():
    global db_client
    if db_client:
        db_client.close()
        print("Disconnected from MongoDB")

# Models
class LoginRequest(BaseModel):
    username: str
    password: str

# Agents
class LoginMonitorAgent:
    async def monitor_login_attempts(self, username: str, is_valid: bool):
        if not is_valid:
            failed_logins = await db["invalid_login"].find_one({"username": username})
            if failed_logins:
                await db["invalid_login"].update_one({"username": username}, {"$inc": {"count": 1}})
            else:
                await db["invalid_login"].insert_one({"username": username, "count": 1})
            print(f"Login attempt for {username} recorded in invalid_login collection.")
        else:
            print(f"Login for {username} is valid, not recording failed attempt.")

class ProfileLockAgent:
    async def lock_profile(self, username: str):
        failed_logins = await db["invalid_login"].find_one({"username": username})
        if failed_logins and failed_logins["count"] >= 3:
            print(f"Profile for {username} is locked due to multiple failed attempts.")
            return True
        return False

class AdminAlertAgent:
    active_connections: List[WebSocket] = []

    async def send_alert(self, username: str):
        message = f"Admin Alert: User {username} has failed 3 or more login attempts."
        print(message)  # Logs the alert in the backend terminal
        for connection in self.active_connections:
            await connection.send_text(message)

    async def websocket_endpoint(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                print(f"Received message from client: {data}")
        except WebSocketDisconnect:
            self.active_connections.remove(websocket)

# Routes
@app.post("/login")
async def login(request: LoginRequest):
    username = request.username
    password = request.password

    # Create agents
    login_monitor_agent = LoginMonitorAgent()
    profile_lock_agent = ProfileLockAgent()
    admin_alert_agent = AdminAlertAgent()

    # Check if profile is locked
    if await profile_lock_agent.lock_profile(username):
        raise HTTPException(status_code=403, detail="Your account is locked due to multiple failed login attempts.")
    
    # Validate user in all three collections: teacher, student, and admin
    user = await db["student"].find_one({"email": username})
    if not user:
        user = await db["teacher"].find_one({"email": username})
    if not user:
        user = await db["admin"].find_one({"email": username})
    
    # Validate password and record login attempt
    if user and user.get("password") == password:
        await login_monitor_agent.monitor_login_attempts(username, is_valid=True)
        return {"message": "Valid user login"}
    else:
        await login_monitor_agent.monitor_login_attempts(username, is_valid=False)

        # Send admin alert after 3 failed login attempts
        if await profile_lock_agent.lock_profile(username):
            await admin_alert_agent.send_alert(username)

        raise HTTPException(status_code=401, detail="Invalid username or password")

# WebSocket route for real-time admin alerts
@app.websocket("/ws/admin_alert")
async def websocket_admin_alert(websocket: WebSocket):
    alert_agent = AdminAlertAgent()
    await alert_agent.websocket_endpoint(websocket)

@app.get("/invalid_login")
async def get_invalid_login():
    # Return the invalid login collection for monitoring
    invalid_logins = await db["invalid_login"].find().to_list(None)
    return invalid_logins

@app.get("/")
async def read_root():
    return {"message": "Welcome to the LMS system"}
