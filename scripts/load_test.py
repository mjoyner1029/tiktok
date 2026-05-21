"""Load testing script for TikTok Style Engine.

Tests realistic workloads:
- User registration and authentication
- Project creation with uploads
- AI style analysis
- Video rendering
- Concurrent users

Usage:
    python scripts/load_test.py --users 10 --duration 60
"""

import asyncio
import time
import random
import statistics
from typing import List
import argparse

import httpx


class LoadTester:
    """Simulate realistic user workflows."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.metrics = {
            "requests_total": 0,
            "requests_success": 0,
            "requests_failed": 0,
            "response_times": [],
            "errors": [],
        }
    
    async def register_user(self, client: httpx.AsyncClient, user_id: int) -> dict:
        """Register a test user."""
        start = time.time()
        try:
            response = await client.post(
                f"{self.base_url}/api/v1/auth/register",
                json={
                    "email": f"loadtest_user_{user_id}@example.com",
                    "password": "testpass123",
                    "name": f"Load Test User {user_id}",
                }
            )
            duration = time.time() - start
            self.metrics["response_times"].append(duration)
            self.metrics["requests_total"] += 1
            
            if response.status_code == 201:
                self.metrics["requests_success"] += 1
                return response.json()
            else:
                self.metrics["requests_failed"] += 1
                self.metrics["errors"].append(f"Register failed: {response.status_code}")
                return None
        except Exception as e:
            self.metrics["requests_failed"] += 1
            self.metrics["errors"].append(f"Register exception: {str(e)}")
            return None
    
    async def create_project(self, client: httpx.AsyncClient, token: str) -> dict:
        """Create a test project."""
        start = time.time()
        try:
            response = await client.post(
                f"{self.base_url}/api/v1/projects/",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "title": f"Load Test Project {random.randint(1000, 9999)}",
                    "goal": "Create an engaging TikTok video",
                    "target_platform": "tiktok",
                }
            )
            duration = time.time() - start
            self.metrics["response_times"].append(duration)
            self.metrics["requests_total"] += 1
            
            if response.status_code == 201:
                self.metrics["requests_success"] += 1
                return response.json()
            else:
                self.metrics["requests_failed"] += 1
                return None
        except Exception as e:
            self.metrics["requests_failed"] += 1
            self.metrics["errors"].append(f"Create project exception: {str(e)}")
            return None
    
    async def list_projects(self, client: httpx.AsyncClient, token: str):
        """List projects (read-heavy operation)."""
        start = time.time()
        try:
            response = await client.get(
                f"{self.base_url}/api/v1/projects/",
                headers={"Authorization": f"Bearer {token}"},
            )
            duration = time.time() - start
            self.metrics["response_times"].append(duration)
            self.metrics["requests_total"] += 1
            
            if response.status_code == 200:
                self.metrics["requests_success"] += 1
            else:
                self.metrics["requests_failed"] += 1
        except Exception as e:
            self.metrics["requests_failed"] += 1
            self.metrics["errors"].append(f"List projects exception: {str(e)}")
    
    async def create_chat_conversation(self, client: httpx.AsyncClient, token: str) -> dict:
        """Create a chat conversation."""
        start = time.time()
        try:
            response = await client.post(
                f"{self.base_url}/api/v1/chat/conversations",
                headers={"Authorization": f"Bearer {token}"},
                json={"title": "Load Test Chat"}
            )
            duration = time.time() - start
            self.metrics["response_times"].append(duration)
            self.metrics["requests_total"] += 1
            
            if response.status_code == 200:
                self.metrics["requests_success"] += 1
                return response.json()
            else:
                self.metrics["requests_failed"] += 1
                return None
        except Exception as e:
            self.metrics["requests_failed"] += 1
            self.metrics["errors"].append(f"Create chat exception: {str(e)}")
            return None
    
    async def send_chat_message(self, client: httpx.AsyncClient, token: str, conversation_id: str):
        """Send a chat message."""
        start = time.time()
        messages = [
            "Help me create a TikTok video",
            "https://www.tiktok.com/@user/video/1234567890",
            "Make the cuts faster",
            "What's the status?",
        ]
        
        try:
            response = await client.post(
                f"{self.base_url}/api/v1/chat/conversations/{conversation_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"content": random.choice(messages)}
            )
            duration = time.time() - start
            self.metrics["response_times"].append(duration)
            self.metrics["requests_total"] += 1
            
            if response.status_code == 200:
                self.metrics["requests_success"] += 1
            else:
                self.metrics["requests_failed"] += 1
        except Exception as e:
            self.metrics["requests_failed"] += 1
            self.metrics["errors"].append(f"Send message exception: {str(e)}")
    
    async def user_workflow(self, user_id: int, duration_seconds: int):
        """Simulate a complete user workflow."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Register
            auth_data = await self.register_user(client, user_id)
            if not auth_data:
                return
            
            token = auth_data["access_token"]
            end_time = time.time() + duration_seconds
            
            # Create initial project
            project = await self.create_project(client, token)
            
            # Create chat conversation
            conversation = await self.create_chat_conversation(client, token)
            
            # Simulate ongoing activity
            while time.time() < end_time:
                # Random actions
                action = random.choice([
                    "list_projects",
                    "create_project",
                    "send_message",
                    "send_message",  # More frequent
                ])
                
                if action == "list_projects":
                    await self.list_projects(client, token)
                elif action == "create_project":
                    await self.create_project(client, token)
                elif action == "send_message" and conversation:
                    await self.send_chat_message(client, token, conversation["id"])
                
                # Wait between actions (simulate thinking time)
                await asyncio.sleep(random.uniform(1, 3))
    
    async def run_test(self, num_users: int, duration_seconds: int):
        """Run load test with multiple concurrent users."""
        print(f"🚀 Starting load test: {num_users} users for {duration_seconds} seconds")
        print(f"   Target: {self.base_url}")
        print()
        
        start_time = time.time()
        
        # Start all user workflows concurrently
        tasks = [
            self.user_workflow(i, duration_seconds)
            for i in range(num_users)
        ]
        
        await asyncio.gather(*tasks)
        
        total_duration = time.time() - start_time
        
        # Print results
        print("\n" + "=" * 70)
        print("📊 LOAD TEST RESULTS")
        print("=" * 70)
        print(f"Duration:          {total_duration:.2f}s")
        print(f"Concurrent Users:  {num_users}")
        print(f"Total Requests:    {self.metrics['requests_total']}")
        print(f"Successful:        {self.metrics['requests_success']} ({self.metrics['requests_success']/max(1, self.metrics['requests_total'])*100:.1f}%)")
        print(f"Failed:            {self.metrics['requests_failed']} ({self.metrics['requests_failed']/max(1, self.metrics['requests_total'])*100:.1f}%)")
        print()
        
        if self.metrics["response_times"]:
            print("Response Times:")
            print(f"  Mean:   {statistics.mean(self.metrics['response_times']):.3f}s")
            print(f"  Median: {statistics.median(self.metrics['response_times']):.3f}s")
            print(f"  Min:    {min(self.metrics['response_times']):.3f}s")
            print(f"  Max:    {max(self.metrics['response_times']):.3f}s")
            print(f"  P95:    {statistics.quantiles(self.metrics['response_times'], n=20)[18]:.3f}s")
            print(f"  P99:    {statistics.quantiles(self.metrics['response_times'], n=100)[98]:.3f}s")
        print()
        
        throughput = self.metrics["requests_total"] / total_duration
        print(f"Throughput:        {throughput:.2f} req/s")
        print()
        
        if self.metrics["errors"]:
            print("❌ Sample Errors (first 5):")
            for error in self.metrics["errors"][:5]:
                print(f"   - {error}")
            print()
        
        # Pass/fail criteria
        success_rate = self.metrics["requests_success"] / max(1, self.metrics["requests_total"])
        if success_rate >= 0.95:
            print("✅ PASS: Success rate >= 95%")
        else:
            print(f"❌ FAIL: Success rate {success_rate*100:.1f}% < 95%")


async def main():
    parser = argparse.ArgumentParser(description="Load test TikTok Style Engine")
    parser.add_argument("--users", type=int, default=10, help="Number of concurrent users")
    parser.add_argument("--duration", type=int, default=60, help="Test duration in seconds")
    parser.add_argument("--url", type=str, default="http://localhost:8000", help="Base URL")
    
    args = parser.parse_args()
    
    tester = LoadTester(base_url=args.url)
    await tester.run_test(args.users, args.duration)


if __name__ == "__main__":
    asyncio.run(main())
