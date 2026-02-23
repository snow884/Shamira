

import os
from fastapi.testclient import TestClient
from server import get_db_session  # Import your FastAPI application
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from server import Base as OriginalBase, app
from utils import encrypt_message, decrypt_message, generate_key_pair, sign_message



os.remove("./test_db.db") if os.path.exists("./test_db.db") else None
# engine = create_engine(
#     SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, echo=True
# )
# TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
#Base = declarative_base()

# Define an override for the database dependency for testing (e.g., an in-memory SQLite DB)

def get_engine():
    return create_engine(
        "sqlite:///./test_db.db", connect_args={"check_same_thread": False}
    )
    
def override_get_db():

    # yield test database session
    print("Creating new test database session")
    
    engine = get_engine()
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    #OriginalBase.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    yield db



import unittest

def register_and_get_token(client: TestClient) -> str:
    
    priv_key, pub_key = generate_key_pair()
    pub_key_serialized = pub_key.serialize()
    response = client.post("/auth/register", json={"public_key": pub_key_serialized, "hostname": "authhost", "alias": "authalias","description":"authdescription"})
    response.raise_for_status()
    
    return pub_key_serialized, priv_key

def login_and_get_token(client: TestClient, priv_key, pub_key_serialized) -> str:
    message = os.urandom(16)  # Random challenge message
    signature = sign_message(priv_key, message)
    
    data = {
        "public_key": pub_key_serialized,
        "message": message.hex(),
        "signature": {
            "r": signature[0],
            "s": signature[1]
        }
    }
    response = client.post("/auth/login", json=data)
    response.raise_for_status()
    return response.json()["token"]

class TestNodeMethods(unittest.TestCase):
    
    def setUp(self):
        self.app = app
        OriginalBase.metadata.create_all(bind=get_engine())
        self.app.dependency_overrides[get_db_session] = override_get_db
        self.client = TestClient(self.app)
        
        self.pub_key_serialized, self.priv_key = register_and_get_token(self.client)
        self.token = login_and_get_token(self.client, self.priv_key, self.pub_key_serialized)
        self.client.headers.update({"Authorization": f"Bearer {self.token}"})

    def tearDown(self):
        self.app.dependency_overrides = {} 
        OriginalBase.metadata.drop_all(bind=get_engine())
        
    def test_list_nodes(self):
        """Test the GET /health endpoint."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"Hello": "World"})

        response = self.client.get("/nodes")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertDictEqual(response.json()[0], {'id': 1, 'hostname': 'authhost', 'alias': 'authalias', 'is_current_node': False, 'description': 'authdescription', 'health_status': 'healthy', 'public_key': self.pub_key_serialized})

class TestAuthMethods(unittest.TestCase):
    
    def setUp(self):
        self.app = app
        OriginalBase.metadata.create_all(bind=get_engine())
        self.app.dependency_overrides[get_db_session] = override_get_db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides = {}  # Clear overrides
        OriginalBase.metadata.drop_all(bind=get_engine())

    def test_register_and_login(self):
        """Test the POST /auth/register and /auth/login endpoints."""
        response = self.client.post("/node", json={"hostname": "authhost", "alias": "authalias","description":"authdescription","is_current_node":True})
        self.assertEqual(response.status_code, 401)  # Unauthorized without token
        self.assertEqual(response.json(), {"detail": "Not authenticated"})

        priv_key, pub_key = generate_key_pair()
        
        pub_key_serialized = pub_key.serialize()

        response = self.client.post("/auth/register", json={"public_key": pub_key_serialized, "hostname": "authhost", "alias": "authalias","description":"authdescription"})
        print(response.json())
        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(response.json(),  {'alias': 'authalias', 'id': 1, 'description': 'authdescription', 'is_current_node': False, 'hostname': 'authhost', 'public_key': pub_key_serialized, 'health_status': 'healthy'})
        
        message = os.urandom(16)  # Random challenge message
        signature = sign_message(priv_key, message)
        
        data = {
            "public_key": pub_key_serialized,
            "message": message.hex(),
            "signature": {
                "r": signature[0],
                "s": signature[1]
            }
        }
        response = self.client.post("/auth/login", json=data)
        
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json())
        self.assertIn("expiration_time", response.json())

        token = response.json()["token"]
        response = self.client.get("/nodes", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200)


class TesJobMethods(unittest.TestCase):
    
    def setUp(self):
        self.app = app
        OriginalBase.metadata.create_all(bind=get_engine())
        self.app.dependency_overrides[get_db_session] = override_get_db
        self.client = TestClient(self.app)
        
        self.pub_key_serialized, self.priv_key = register_and_get_token(self.client)
        self.token = login_and_get_token(self.client, self.priv_key, self.pub_key_serialized)
        self.client.headers.update({"Authorization": f"Bearer {self.token}"})

    def tearDown(self):
        self.app.dependency_overrides = {}  # Clear overrides
        OriginalBase.metadata.drop_all(bind=get_engine())

    def test_add_job(self):
        """Test the POST /job endpoint."""
        job_data = {
            "name": "testjob",
            "status": "waiting",
            "steps": []
        }
        response = self.client.post("/job", json=job_data)

        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.json())
        self.assertEqual(response.json()["name"], "testjob")
        
if __name__ == '__main__':
    unittest.main()