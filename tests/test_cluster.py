

import hashlib
import json
import os
import uuid
import uuid
from fastapi.testclient import TestClient
from server import Job, JobSchema, Step, StepSchema, StepStatusEnum, VariableTypeEnum, execute_jobs, get_current_node, get_db_session, load_initial_nodes, propagate_jobs, propagate_nodes, start_stop_jobs, JobStatusEnum  # Import your FastAPI application
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from server import Base as OriginalBase, app, Node, generate_keypair, ShamiraTask
from utils import EncryptedFiniteFieldElement, FiniteFieldElement, Point, PolynomialPoint, encrypt_message, decrypt_message, generate_key_pair, lagrange_interpolation, sign_message, get_shares, P, N
import copy

from client import ShamiraClient

def get_engine(node_host=None):
    return create_engine(
        f"sqlite:///./tests/data/{node_host}.db", connect_args={"check_same_thread": False}
    )
    

import unittest

NUM_NODES = 3

def generate_dashboard(node_host="test_node_0"):
    client = get_client_session(node_host=node_host)
    dashboard = client.get("/dashboard")
    dashboard.raise_for_status()
    with open("./tests/data/dashboard.html", "w", encoding="utf-8") as handle:
        handle.write(dashboard.text)
    
        
def get_client_session(node_host):
    print("Getting test client session for node host:", node_host)
    node_id = int(node_host.replace("test_node_", ""))
    
    def override_get_db():

        # yield test database session
        print("Creating new test database session for node host:", f"test_node_{node_id}")
        
        engine = get_engine(f"test_node_{node_id}")
        TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        #OriginalBase.metadata.create_all(bind=engine)
        db = TestingSessionLocal()
        yield db
        
    app.dependency_overrides[get_db_session] = override_get_db

    client = TestClient(app)
    
    return client

def generate_steps_in_db(db, variable:ShamiraTask):
    db_job_data = variable.job.model_dump(exclude={"steps"})
    if not db_job_data.get("hash_id"):
        db_job_data["hash_id"] = f"job-{uuid.uuid4().hex}"
    db_job = Job(**db_job_data)
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    for step_schema in variable.job.steps:
        step_data = step_schema.model_dump()
        db_step = Step(**step_data)
        db_step.job_hash_id = db_job.hash_id
        db.add(db_step)
    db.commit() 
    
def get_db(node_host):
    engine = get_engine(node_host=node_host)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    return db

def execute_all_steps(output:ShamiraTask):
    
    for j in range(len(output.job.steps)*2):
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_jobs(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

        for i in range(NUM_NODES):
            start_stop_jobs(db=get_db(f"test_node_{i}"), get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_jobs(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

        for i in range(NUM_NODES):
            execute_jobs(db=get_db(f"test_node_{i}"), get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
            
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_jobs(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
def reconstruct_sharded_value(db, variable_name, public_key_to_node_index):
    steps = db.query(Step).filter(Step.variable_name == variable_name, Step.variable_type=="shard").all()

    steps_sorted = sorted(steps, key=lambda s: public_key_to_node_index.get(s.dest_node_public_key, 0))

    pol = []
    for step in steps_sorted:
        node_index = public_key_to_node_index.get(step.dest_node_public_key, 0)
        _, private_key = generate_keypair(keypair_file_path=f"tests/data/test_node_{node_index}_keypair.pem")
        output_x_encrypted = EncryptedFiniteFieldElement(None,None).deserialize(json.loads(step.output_value)['x'])
        output_y_encrypted = EncryptedFiniteFieldElement(None,None).deserialize(json.loads(step.output_value)['y'])
        output_x = output_x_encrypted.decrypt(private_key)
        output_y = output_y_encrypted.decrypt(private_key)
        point = PolynomialPoint(output_x, output_y)
        pol.append(point)

    reconstructed_point = lagrange_interpolation(
        pol, FiniteFieldElement(0, N)
    )
    
    return reconstructed_point.value

class TestNodePropagation(unittest.TestCase):
    
    def setUp(self):
        self.app = {}
        self.client = {}
        
        for i in range(NUM_NODES):
            self.app[i] = copy.copy(app)
            OriginalBase.metadata.create_all(bind=get_engine(node_host=f"test_node_{i}"))
            
        
    def tearDown(self):
        for i in range(NUM_NODES):
            self.app[i].dependency_overrides = {} 
            OriginalBase.metadata.drop_all(bind=get_engine(f"test_node_{i}"))
        
    def test_propagate_nodes(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(i+1) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        nodes_stored = {}
        nodes_stored_compare = {}
        
        for i in range(NUM_NODES):
            print("--------------------------------")
            print(f"Verifying nodes in test_node_{i} database:")
            db = get_db(f"test_node_{i}")
            nodes = db.query(Node).all()
            for node in nodes:
                nodes_stored = {}
                print(f"Node in test_node_{i} DB: {node.hostname}, {node.alias}, {node.description}, {node.public_key}")
                nodes_stored[node.hostname] = {
                    "hostname": node.hostname,
                    "alias": node.alias,
                    "description": node.description,
                    "public_key": node.public_key,
                }
            if nodes_stored_compare == {}:
                nodes_stored_compare = copy.deepcopy(nodes_stored)
            else:
                self.assertDictEqual(nodes_stored, nodes_stored_compare)
             
    def test_propagate_jobs(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

        db = get_db(f"test_node_0")
        nodes = db.query(Node).order_by(Node.id).all()
        public_key_to_node_index = {node.public_key: idx for idx, node in enumerate(nodes)}
        new_job = Job(
            name = "Test Job",
            status = "waiting",
            hash_id = f"job-{uuid.uuid4().hex}"
        )
        db.add(new_job)
        for node in db.query(Node).all(): 
            db_step =  Step(
                status="waiting",
                parameters=None,
                step_type="init_variable",
                hash_id = f"step-{uuid.uuid4().hex}",
                variable_name="test_var",
                variable_type="shard",
                assigned_node_public_key=node.public_key,
                dest_node_public_key=node.public_key,
            ) 
            db_step.job_hash_id = new_job.hash_id
            db.add(db_step)
            
        db.add(new_job)
        db.commit()
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_jobs(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

        for i in range(NUM_NODES):
            print("--------------------------------")
            print(f"Verifying jobs in test_node_{i} database:")
            db = get_db(f"test_node_{i}")
            jobs = db.query(Job).all()
            self.assertEqual(len(jobs), 1)
            for job in jobs:
                print(f"Job in test_node_{i} DB: {job.name},{job.status}")
                self.assertEqual(job.name, "Test Job")


    def test_execute_jobs2(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        db = get_db(f"test_node_0")
        
        a = ShamiraTask(db, "a", 4, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        b = ShamiraTask(db, "b", 5, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        y = ShamiraTask(db, "y", a + b, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        generate_steps_in_db(db, y)

        execute_all_steps(y)

        db = get_db(f"test_node_0")
       
        nodes = db.query(Node).order_by(Node.id).all()
        public_key_to_node_index = {node.public_key: idx for idx, node in enumerate(nodes)}
        
        steps = db.query(Step).filter(Step.variable_name == "y", Step.variable_type=="shard", Step.status=="completed").all()
        generate_dashboard()
        self.assertEqual(len(steps), NUM_NODES, "Number of steps for variable y should be equal to number of nodes")
        
        reconstructed_point_value = reconstruct_sharded_value(db, "y", public_key_to_node_index)
        
        self.assertEqual(reconstructed_point_value, 9)  # 5 + 4


    def test_shards_to_open_var(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        db = get_db(f"test_node_0")
        
        a = ShamiraTask(db, "a", 4, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        b = ShamiraTask(db, "b", 5, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        y = ShamiraTask(db, "y", a + b, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        c = ShamiraTask(db, "c", 5, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        z = ShamiraTask(db, "z", c * y, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        generate_steps_in_db(db, z)
        
        try:
            execute_all_steps(y)
        except Exception as e:    
            generate_dashboard()
            raise e
        generate_dashboard()
        db = get_db(f"test_node_0")
        nodes = db.query(Node).order_by(Node.id).all()
        public_key_to_node_index = {node.public_key: idx for idx, node in enumerate(nodes)}
        
        
        
        steps = db.query(Step).filter(Step.variable_name == "z", Step.variable_type=="private_variable").all()

        self.assertEqual(len(steps), 1, "Number of steps for variable z should be equal to 1")
        print(steps[0].output_value)
        encrypted_value = EncryptedFiniteFieldElement(None,None).deserialize(steps[0].output_value)
        private_key = generate_keypair(keypair_file_path=f"tests/data/test_node_0_keypair.pem")[1]
        decrypted_value = encrypted_value.decrypt(private_key).value
        
        self.assertEqual(decrypted_value, 5 * 9)  # 5 * 9

    def test_execute_job_multiplication(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        db = get_db(f"test_node_0")
        
        a = ShamiraTask(db, "a", 4, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        b = ShamiraTask(db, "b", 5, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        y = ShamiraTask(db, "y", a * b, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        generate_steps_in_db(db, y)
        
        try:
            for i in range(len(y.job.steps)+2):
                
                for i in range( NUM_NODES):
                    db = get_db(f"test_node_{i}")
                    propagate_jobs(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

                for i in range(NUM_NODES):
                    start_stop_jobs(db=get_db(f"test_node_{i}"), get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

                for i in range( NUM_NODES):
                    db = get_db(f"test_node_{i}")
                    propagate_jobs(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

                for i in range(NUM_NODES):
                    execute_jobs(db=get_db(f"test_node_{i}"), get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        except Exception as e:    
            generate_dashboard()
            raise e
        
        db = get_db(f"test_node_0")
        nodes = db.query(Node).order_by(Node.id).all()
        public_key_to_node_index = {node.public_key: idx for idx, node in enumerate(nodes)}
        
        steps = db.query(Step).filter(Step.variable_name == "y", Step.variable_type=="shard").all()

        self.assertEqual(len(steps), NUM_NODES, "Number of steps for variable y should be equal to number of nodes")
        
        reconstructed_point_value = reconstruct_sharded_value(db, "y", public_key_to_node_index)
        
        self.assertEqual(reconstructed_point_value, 20)  # 5 * 4

    def test_execute_with_shards_hard(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        db = get_db(f"test_node_0")
        
        a = ShamiraTask(db, "a", 4, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        b = ShamiraTask(db, "b", 5, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        d = ShamiraTask(db, "d", 7, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        y = ShamiraTask(db, "y", (a + b)-d, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        generate_steps_in_db(db, y)
        
        execute_all_steps(y)

        db = get_db(f"test_node_0")
        nodes = db.query(Node).order_by(Node.id).all()
        public_key_to_node_index = {node.public_key: idx for idx, node in enumerate(nodes)}
        
        steps = db.query(Step).filter(Step.variable_name == "y", Step.variable_type=="shard").all()

        self.assertEqual(len(steps), NUM_NODES, "Number of steps for variable y should be equal to number of nodes")
        
        reconstructed_point_value = reconstruct_sharded_value(db, "y", public_key_to_node_index)

        self.assertEqual(reconstructed_point_value, (4 + 5)-7)  



    def test_private_computation_to_shards(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        db = get_db(f"test_node_0")
        
        a1 = ShamiraTask(db, "a1", 2, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        a2 = ShamiraTask(db, "a2", 2, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        a = ShamiraTask(db, "a", a1+a2, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        a_sharded = ShamiraTask(db, 'a_sharded', a, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")

        b1 = ShamiraTask(db, "b1", 3, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        b2 = ShamiraTask(db, "b2", 2, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        b = ShamiraTask(db, "b", b1+b2, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        b_sharded = ShamiraTask(db, 'b_sharded', b, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")

        c_sharded = a_sharded + b_sharded
        
        generate_steps_in_db(db, c_sharded)
        try:
            for iteration in range(len(c_sharded.job.steps)+5):
                
                for i in range( NUM_NODES):
                    db = get_db(f"test_node_{i}")
                    propagate_jobs(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

                for i in range(NUM_NODES):
                    start_stop_jobs(db=get_db(f"test_node_{i}"), get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

                for i in range( NUM_NODES):
                    db = get_db(f"test_node_{i}")
                    propagate_jobs(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")

                for i in range(NUM_NODES):
                    execute_jobs(db=get_db(f"test_node_{i}"), get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        except Exception as e:    
            generate_dashboard()
            raise e
        
        # First, verify that the sharding steps work correctly
        # Get sharding steps for a_sharded
        a_shard_steps = db.query(Step).filter(
            Step.variable_name == "a_sharded",
            Step.variable_type == "shard"
        ).all()
        
        print(f"\nFound {len(a_shard_steps)} steps for a_sharded sharding")
        
        # Get sharding steps for b_sharded
        b_shard_steps = db.query(Step).filter(
            Step.variable_name == "b_sharded",
            Step.variable_type == "shard"
        ).all()
        
        print(f"Found {len(b_shard_steps)} steps for b_sharded sharding")
        
        # For now, just verify we're finding these steps
        self.assertEqual(len(a_shard_steps), NUM_NODES, f"Should have {NUM_NODES} sharding steps for a_sharded")
        self.assertEqual(len(b_shard_steps), NUM_NODES, f"Should have {NUM_NODES} sharding steps for b_sharded")
        
        # Get all steps for the addition operation (3 total - one for each node)
        steps = db.query(Step).filter(
            Step.variable_name == "(a_sharded) + (b_sharded)",
            Step.variable_type == "shard",
            Step.step_type == "add"
        ).all()
        
        print(f"Found {len(steps)} steps for addition operation")
        self.assertEqual(len(steps), 3, "Should have 3 addition steps (one per node)")  




    def test_execute_jobs_private_vars(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        db = get_db(f"test_node_0")
        
        a = ShamiraTask(db, "a", 4, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        b = ShamiraTask(db, "b", 5, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        d = ShamiraTask(db, "d", 7, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        y = ShamiraTask(db, "y", (a + b)-d, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        generate_steps_in_db(db, y)

        execute_all_steps(y)
        
        db = get_db(f"test_node_0")
        nodes = db.query(Node).order_by(Node.id).all()
        public_key_to_node_index = {node.public_key: idx for idx, node in enumerate(nodes)}
        
        steps = db.query(Step).filter(Step.variable_name == "y", Step.variable_type=="private_variable").all()

        self.assertTrue(len(steps) >= 1, "Expected at least one step for private variable y")

        steps_sorted = sorted(steps, key=lambda s: public_key_to_node_index.get(s.dest_node_public_key, 0))

        output_values = []
        for step_item in steps_sorted:
            node_index = public_key_to_node_index.get(step_item.dest_node_public_key, 0)
            _, private_key = generate_keypair(keypair_file_path=f"tests/data/test_node_{node_index}_keypair.pem")
            output_x_encrypted = EncryptedFiniteFieldElement(None, None).deserialize(step_item.output_value)
            output_x = output_x_encrypted.decrypt(private_key)
            output_values.append(output_x)

        self.assertTrue(all(val == output_values[0] for val in output_values), "All private variable outputs should match")
        self.assertEqual(output_values[0], (4 + 5)-7)


    def test_test_priv_var_to_shard_assignment(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        db = get_db(f"test_node_0")
        
        a_priv_var = ShamiraTask(db, "a_pv", 4, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        y = ShamiraTask(db, "y", a_priv_var, VariableTypeEnum.shard, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        generate_steps_in_db(db, y)
        
        execute_all_steps(y)
        
        public_key, private_key = generate_keypair(keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        db = get_db(f"test_node_0")
        
        # db = get_db(f"test_node_0")
        nodes = db.query(Node).order_by(Node.id).all()
        public_key_to_node_index = {node.public_key: idx for idx, node in enumerate(nodes)}
        steps = db.query(Step).filter(Step.variable_name == "y", Step.variable_type=="shard").all()

        self.assertEqual(len(steps), NUM_NODES, "Number of steps for variable y should be equal to number of nodes")

        reconstructed_point_value = reconstruct_sharded_value(db, "y", public_key_to_node_index)
        
        self.assertEqual(reconstructed_point_value, 4 )  


    def test_test_affine_transform(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        db = get_db(f"test_node_0")
        
        nodes = db.query(Node).all()
        
        node_alice = nodes[0]
        node_bob = nodes[1]
        
        a = ShamiraTask(get_db(f"test_node_1"), "a", 12, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_1_keypair.pem")
        
        x = ShamiraTask(get_db(f"test_node_0"), "x", 13, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        r = ShamiraTask(get_db(f"test_node_1"), "r", 14, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_1_keypair.pem")
        
        y = x.compute_affine(a, r, source_node_public_key=node_bob.public_key, dest_node_public_key=node_alice.public_key, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        generate_steps_in_db(db, y)
        
        execute_all_steps(y)
        
        public_key, private_key = generate_keypair(keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        db = get_db(f"test_node_0")
        
        db = get_db(f"test_node_0")
        step = db.query(Step).filter(
            Step.variable_name == y._name,
            Step.variable_type=="private_variable",
            Step.dest_node_public_key==get_current_node(get_db(f"test_node_0")).public_key
        ).first()

        generate_dashboard()

        #print(f"Step output value: {step.output_value}")
        #print(json.loads(step.output_value)['x'])
        output_x_encrypted = step.output_value
        #print("Private key:")
        #print(private_key)
        output_x = EncryptedFiniteFieldElement(None, None).deserialize(output_x_encrypted)
        output_x = output_x.decrypt(private_key)

        db = get_db(f"test_node_1")

        generate_dashboard()

        print(f"Reconstructed point value: {output_x}")
        self.assertEqual(output_x, 12*13 + 14)  
        
        self.assertEqual(y._name, "Enc(x)*a + r")


    def test_affine_transform_with_computation(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        db = get_db(f"test_node_0")
        
        nodes = db.query(Node).all()
        
        node_alice = nodes[0]
        node_bob = nodes[1]
        
        a = ShamiraTask(get_db(f"test_node_1"), "a", 12, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_1_keypair.pem")
        
        x = ShamiraTask(get_db(f"test_node_0"), "x", 13, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        r = ShamiraTask(get_db(f"test_node_1"), "r", 14, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_1_keypair.pem")
        
        z = x.compute_affine(a, r, source_node_public_key=node_bob.public_key, dest_node_public_key=node_alice.public_key, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        b = ShamiraTask(get_db(f"test_node_0"), "b", 1, VariableTypeEnum.private_variable, keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        y = z + b
        
        generate_steps_in_db(db, y)
        
        execute_all_steps(y)
        
        public_key, private_key = generate_keypair(keypair_file_path=f"tests/data/test_node_0_keypair.pem")
        
        db = get_db(f"test_node_0")
        
        db = get_db(f"test_node_0")
        step = db.query(Step).filter(Step.variable_name == "(Enc(x)*a + r) + (b)", Step.variable_type=="private_variable", Step.dest_node_public_key==get_current_node(get_db(f"test_node_0")).public_key).first()

        generate_dashboard()

        #print(f"Step output value: {step.output_value}")
        #print(json.loads(step.output_value)['x'])
        output_x_encrypted = step.output_value
        #print("Private key:")
        #print(private_key)
        
        self.assertEqual(step.status, StepStatusEnum.completed, "Y Step should be completed")
        
        output_x = EncryptedFiniteFieldElement(None, None).deserialize(output_x_encrypted)
        output_x = output_x.decrypt(private_key)

        db = get_db(f"test_node_1")

        generate_dashboard()

        print(f"Reconstructed point value: {output_x}")
        self.assertEqual(output_x, 12*13 + 14 +1)  
        
        self.assertEqual(y._name, "(Enc(x)*a + r) + (b)")

    def test_beaver_triple_gen(self):

        for i in range(NUM_NODES):
            db = get_db(f"test_node_{i}")
            initial_nodes_override = [
                {
                    "hostname": f"test_node_{j}",
                    "alias": f"Test Node {j}",
                    "description": f"This is test node {j}",
                    "is_current_node": True if j == i else False,
                }
                for j in range(NUM_NODES) 
            ]
            load_initial_nodes(db=db, initial_nodes_override=initial_nodes_override, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        
        for i in range( NUM_NODES):
            db = get_db(f"test_node_{i}")
            propagate_nodes(db=db, get_client_session=get_client_session, keypair_file_path=f"tests/data/test_node_{i}_keypair.pem")
        db = get_db(f"test_node_0")
        
        a,b,c = ShamiraTask(db=db,name="beaver_triple",value=None,variable_type=VariableTypeEnum.shard).generate_beaver_tripple()
        
        ShamiraTask(db=db,name="(Aa_sharded) + (Ab_sharded)",value=a,variable_type=VariableTypeEnum.private_variable)
        ShamiraTask(db=db,name="(Ba_sharded) + (Bb_sharded)",value=b,variable_type=VariableTypeEnum.private_variable)
        ShamiraTask(db=db,name="(Ca_sharded) + (Cb_sharded)",value=c,variable_type=VariableTypeEnum.private_variable)
        
        db_job_data = c.job.model_dump(exclude={"steps"})
        if not db_job_data.get("hash_id"):
            db_job_data["hash_id"] = f"job-{uuid.uuid4().hex}"
        db_job = Job(**db_job_data)
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        
        steps_all = []

        for s in c.job.steps + a.job.steps + b.job.steps:
            
            if s.hash_id not in [step.hash_id for step in steps_all]:
                steps_all.append(s)
        
        for step_schema in steps_all:
            step_data = step_schema.model_dump()
            db_step = Step(**step_data)
            db_step.job_hash_id = db_job.hash_id
            if not db_step.hash_id:
                db_step.hash_id = f"step-{uuid.uuid4().hex}"
            db.add(db_step)
            
        db.commit()
    
        execute_all_steps(c)
        
        db = get_db(f"test_node_1")

        generate_dashboard()
        
        # Create a mapping from public key to node index
        nodes = db.query(Node).order_by(Node.id).all()
        public_key_to_node_index = {}
        for idx, node in enumerate(nodes):
            public_key_to_node_index[node.public_key] = idx
        
        all_steps = db.query(Step).all()
        for step in all_steps:
            self.assertEqual(step.status, StepStatusEnum.completed, f"Step {step} should be completed")
        
        Aa_step = db.query(Step).filter(Step.variable_name == "Aa", Step.variable_type=="private_variable").first()

        output_encrypted = EncryptedFiniteFieldElement(None,None).deserialize(Aa_step.output_value)
        public_key, private_key = generate_keypair(keypair_file_path=f"tests/data/test_node_0_keypair.pem")
                
        aa_output = output_encrypted.decrypt(private_key)
        
        
        Ab_step = db.query(Step).filter(Step.variable_name == "Ab", Step.variable_type=="private_variable").first()

        output_encrypted = EncryptedFiniteFieldElement(None,None).deserialize(Ab_step.output_value)
        public_key, private_key = generate_keypair(keypair_file_path=f"tests/data/test_node_1_keypair.pem")
                
        ab_output = output_encrypted.decrypt(private_key)
        
        Ba_step = db.query(Step).filter(Step.variable_name == "Ba", Step.variable_type=="private_variable").first()

        output_encrypted = EncryptedFiniteFieldElement(None,None).deserialize(Ba_step.output_value)
        public_key, private_key = generate_keypair(keypair_file_path=f"tests/data/test_node_0_keypair.pem")
                
        ba_output = output_encrypted.decrypt(private_key)
        
        
        Bb_step = db.query(Step).filter(Step.variable_name == "Bb", Step.variable_type=="private_variable").first()

        output_encrypted = EncryptedFiniteFieldElement(None,None).deserialize(Bb_step.output_value)
        public_key, private_key = generate_keypair(keypair_file_path=f"tests/data/test_node_1_keypair.pem")
                
        bb_output = output_encrypted.decrypt(private_key)
                
        beaver_tripple_values = {}
        for var_name in ["(Aa_sharded) + (Ab_sharded)", "(Ba_sharded) + (Bb_sharded)", "(Ca_sharded) + (Cb_sharded)"]:
            
            steps = db.query(Step).filter(Step.variable_name == var_name, Step.variable_type=="shard").all()
            
            self.assertEqual(len(steps), NUM_NODES, f"Number of steps for variable {var_name} should be equal to number of nodes")
            
            reconstructed_point_value = reconstruct_sharded_value(db, var_name, public_key_to_node_index)
            
            beaver_tripple_values[var_name] = reconstructed_point_value
        
        self.assertEqual(beaver_tripple_values["(Ba_sharded) + (Bb_sharded)"],ba_output+bb_output)
        self.assertEqual(beaver_tripple_values["(Aa_sharded) + (Ab_sharded)"],aa_output+ab_output)
        
        self.assertEqual((ba_output+bb_output) * (aa_output+ab_output), beaver_tripple_values["(Ca_sharded) + (Cb_sharded)"])  
        
        self.assertEqual(beaver_tripple_values["(Aa_sharded) + (Ab_sharded)"] * beaver_tripple_values["(Ba_sharded) + (Bb_sharded)"], beaver_tripple_values["(Ca_sharded) + (Cb_sharded)"])  
        
        
    