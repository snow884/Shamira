

import os
from fastapi.testclient import TestClient
from server import get_db_session  # Import your FastAPI application
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from server import Base as OriginalBase, app
from utils import encrypt_message, decrypt_message, generate_key_pair, sign_message

from server import NodeSchema

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

from client import ShamiraClient

class TestShamiraClient(unittest.TestCase):
    
    def setUp(self):
        self.app = app
        OriginalBase.metadata.create_all(bind=get_engine())
        self.app.dependency_overrides[get_db_session] = override_get_db
        self.client = TestClient(self.app)
        
    def tearDown(self):
        self.app.dependency_overrides = {} 
        OriginalBase.metadata.drop_all(bind=get_engine())
        
    def test_login_and_list_nodes(self):
        """Test the GET /health endpoint."""
        
        def get_client_session(node_host):
            return self.client
        
        current_node = NodeSchema(
            hostname="localhost",
            alias="local",
            description="Local test node",
            public_key =""
        )

        ShamiraClient_instance = ShamiraClient(
            node_host="localhost:8000",
            current_node=current_node,
            get_session=get_client_session,
            keypair_file_path='test_keypair.key'
        )
        nodes = ShamiraClient_instance.get_nodes()
        self.assertIsInstance(nodes, list)
        self.assertGreaterEqual(len(nodes), 1)  # At least one node should be
        self.assertIn("hostname", nodes[0])
        self.assertIn("alias", nodes[0])
        self.assertIn("description", nodes[0])
        self.assertIn("public_key", nodes[0])

        
        