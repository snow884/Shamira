# Shamira

A distributed multi-party computation (MPC) framework for blockchain applications, built with Python, FastAPI, and REST APIs. Shamira enables secure computation on shared secrets using Shamir's Secret Sharing Scheme over finite fields.

## Overview

Shamira is designed for scenarios where multiple parties need to jointly perform computations on sensitive data without revealing the underlying values to any single party. The framework distributes computation across multiple nodes, where each node holds only a "share" (shard) of the actual data. Through cryptographic techniques including Shamir's Secret Sharing and elliptic curve encryption, the system maintains security while enabling complex arithmetic operations.

### Key Features

- **Distributed Secret Sharing**: Values are split into shards distributed across multiple nodes using Shamir's Secret Sharing
- **Secure Multi-Party Computation**: Perform arithmetic operations (addition, multiplication) on encrypted/sharded data
- **REST API Architecture**: FastAPI-based server with comprehensive REST endpoints for node communication
- **Finite Field Arithmetic**: All operations performed over large prime fields for cryptographic security
- **Elliptic Curve Encryption**: Inter-node communication secured with public-key cryptography
- **Job-Based Execution**: Complex computations defined as directed acyclic graphs (DAGs) of steps
- **Automatic Node Synchronization**: Built-in coordination to ensure all nodes complete required steps before proceeding
- **Health Monitoring**: Node health checks and status tracking
- **Token-Based Authentication**: Secure node-to-node authentication with signature verification

## Architecture

### Variable Types

Shamira supports three types of variables:

- **Private**: Known only to a single node, stored unencrypted locally
- **Shard**: A polynomial point representing a share of a secret, distributed across nodes
- **Public**: Known to all nodes (not yet fully implemented in current version)

### Computation Model

1. **Job Definition**: Define a computation as a job with multiple steps
2. **Step Execution**: Each step specifies an operation (add, multiply, assign) and operands
3. **Distributed Processing**: Nodes execute steps independently, coordinating through REST APIs
4. **Synchronization**: System ensures all dependencies are met before executing dependent steps
5. **Result Reconstruction**: Final results can be reconstructed from shards using Lagrange interpolation

### Node Communication

Nodes communicate via REST APIs:
- Each node runs a FastAPI server
- Nodes register with each other using public keys
- Messages between nodes are encrypted using recipient's public key
- Authentication via JWT tokens with signature verification

## Installation

### Prerequisites

- Python 3.9 or higher
- pip package manager

### Install Dependencies

```bash
pip install -r requirements.txt
```

Required packages:
- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `sqlalchemy` - Database ORM
- `requests` - HTTP client
- `pyyaml` - Configuration parsing
- `apscheduler` - Background task scheduling
- `httpx` - Async HTTP client

## Quick Start

### Running a Single Node Locally

```bash
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

### Running a Multi-Node Cluster

For testing and development, you can run multiple nodes on different ports:

```bash
# Terminal 1 - Node 0
uvicorn server:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 - Node 1
uvicorn server:app --reload --host 0.0.0.0 --port 8001

# Terminal 3 - Node 2
uvicorn server:app --reload --host 0.0.0.0 --port 8002
```

### Using Docker Compose

```bash
cd docker_compose
docker-compose up -d
```

## Configuration

### Node List Configuration

Create an `initial_node_list.yaml` file to define your cluster:

```yaml
nodes:
	- hostname: "http://localhost:8000"
		alias: "node_0"
		description: "Primary computation node"
	- hostname: "http://localhost:8001"
		alias: "node_1"
		description: "Secondary computation node"
	- hostname: "http://localhost:8002"
		alias: "node_2"
		description: "Tertiary computation node"
```

### Database Configuration

By default, Shamira uses SQLite for local storage. The database URL is configured in `server.py`:

```python
SQLALCHEMY_DATABASE_URL = "sqlite:///./local_storage.db"
```

For production, consider using PostgreSQL or another production-grade database.

## API Reference

### Authentication Endpoints

#### POST `/register_node`
Register a new node in the cluster.

**Request Body:**
```json
{
	"hostname": "http://node-address:8000",
	"alias": "node_0",
	"description": "Node description",
	"public_key": "-----BEGIN PUBLIC KEY-----..."
}
```

#### POST `/login`
Authenticate and receive a JWT token.

**Request Body:**
```json
{
	"public_key": "-----BEGIN PUBLIC KEY-----...",
	"message": "authentication_message",
	"signature": {...}
}
```

### Job Execution Endpoints

#### POST `/execute_job`
Submit a job for distributed execution.

**Request Body:**
```json
{
	"name": "multiplication_job",
	"steps": [
		{
			"step_id": "step_1",
			"operation": "assign",
			"destination_node": 1,
			"variable_name": "a",
			"variable_type": "shard",
			"value": 5
		},
		{
			"step_id": "step_2",
			"operation": "multiply",
			"variable_name": "result",
			"operand1": "a",
			"operand2": "b"
		}
	]
}
```

#### GET `/execute_step`
Execute pending steps (called internally by scheduler).

#### POST `/receive_encrypted_variable`
Receive encrypted variable shares from other nodes.

**Request Body:**
```json
{
	"variable_name": "shared_secret",
	"variable_type": "shard",
	"encrypted_value": {...},
	"source_node_id": 1,
	"destination_node_id": 2,
	"step_id": "step_123"
}
```

### Node Management Endpoints

#### GET `/nodes`
List all registered nodes in the cluster.

**Response:**
```json
{
	"nodes": [
		{
			"id": 1,
			"hostname": "http://localhost:8000",
			"alias": "node_0",
			"health_status": "healthy"
		}
	]
}
```

#### GET `/steps`
List all steps (optionally filtered by status).

**Query Parameters:**
- `status`: Filter by step status (pending, in_progress, completed, failed)

#### POST `/set_health_status/{node_id}`
Update a node's health status.

## Usage Examples

### Example 1: Simple Addition of Sharded Values

```python
from client import ShamiraClient

# Initialize client for node
client = ShamiraClient(
		node_host="http://localhost:8000",
		current_node=my_node_info
)

# Define job
job = {
		"name": "add_shards",
		"steps": [
				{
						"step_id": "assign_a",
						"operation": "assign",
						"destination_node": 1,
						"variable_name": "a",
						"variable_type": "shard",
						"value": 10
				},
				{
						"step_id": "assign_b",
						"operation": "assign",
						"destination_node": 1,
						"variable_name": "b",
						"variable_type": "shard",
						"value": 20
				},
				{
						"step_id": "add",
						"operation": "add",
						"variable_name": "result",
						"operand1": "a",
						"operand2": "b",
						"dependencies": ["assign_a", "assign_b"]
				}
		]
}

# Execute job
response = client.execute_job(job)
```

### Example 2: Multiplication of Sharded Values

```python
# Multiplication example (requires all nodes to have shards)
multiplication_job = {
		"name": "multiply_shards",
		"steps": [
				{
						"step_id": "set_x",
						"operation": "assign",
						"destination_node": 1,
						"variable_name": "x",
						"variable_type": "shard",
						"value": 4
				},
				{
						"step_id": "set_y",
						"operation": "assign",
						"destination_node": 1,
						"variable_name": "y",
						"variable_type": "shard",
						"value": 5
				},
				{
						"step_id": "multiply",
						"operation": "multiply",
						"variable_name": "product",
						"operand1": "x",
						"operand2": "y",
						"dependencies": ["set_x", "set_y"]
				}
		]
}

client.execute_job(multiplication_job)
```

## Data Models

### Node
Represents a computation node in the cluster.

**Fields:**
- `id`: Auto-incrementing primary key
- `hostname`: Node's network address
- `public_key`: Node's public key for encryption
- `alias`: Human-readable node identifier
- `is_current_node`: Boolean flag for self-identification
- `description`: Optional node description
- `health_status`: Current health status (healthy, dead, error, none)

### Job
Represents a computation job composed of multiple steps.

**Fields:**
- `id`: Auto-incrementing primary key
- `name`: Job identifier
- `description`: Optional job description
- `status`: Execution status (pending, in_progress, completed, failed)

### Step
Represents a single operation in a job.

**Fields:**
- `id`: Auto-incrementing primary key
- `job_id`: Foreign key to parent job
- `step_id`: Unique step identifier within job
- `operation`: Operation type (assign, add, multiply)
- `variable_name`: Name of result variable
- `variable_type`: Type of variable (private, shard, public)
- `operand1`, `operand2`: Operation inputs
- `status`: Step status (pending, in_progress, completed, failed)
- `destination_node_id`: Node responsible for result
- `dependencies`: JSON list of prerequisite step IDs

### EncryptedVariable
Represents an encrypted/sharded variable received from another node.

**Fields:**
- `id`: Primary key
- `variable_name`: Variable identifier
- `variable_type`: Type (shard, private, public)
- `encrypted_value`: Serialized encrypted value
- `source_node_id`: Sending node
- `destination_node_id`: Receiving node
- `step_id`: Associated step identifier

## Security Considerations

### Cryptographic Primitives

- **Finite Field**: All arithmetic performed modulo large prime N (order of elliptic curve)
- **Elliptic Curve**: Uses standard elliptic curve cryptography for public-key operations
- **Shamir's Secret Sharing**: Threshold scheme with polynomial interpolation
- **Signature Verification**: All node authentication requires valid signatures

### Best Practices

1. **Key Management**: Store private keys securely; never transmit private keys
2. **Network Security**: Use HTTPS/TLS in production environments
3. **Node Authentication**: Always verify node signatures before accepting data
4. **Database Security**: Use encrypted storage for sensitive databases
5. **Access Control**: Implement proper access controls for API endpoints
6. **Audit Logging**: Monitor all node communications for suspicious activity

## Testing

### Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run specific test
python -m pytest tests/test_cluster.py::TestNodePropagation::test_execute_job_multiplication -xvs

# Run with verbose output
python -m pytest tests/ -v
```

### Test Structure

- `test_server.py`: Unit tests for server functionality
- `test_client.py`: Client API tests
- `test_cluster.py`: Multi-node integration tests
- `test_utils.py`: Utility function tests

### Test Environment

Tests use isolated SQLite databases in `tests/data/` directory. Each test node gets its own:
- Database file (`test_node_0.db`, `test_node_1.db`, etc.)
- Key pair file (`test_keypair_0.pem`, etc.)

## Development

### Project Structure

```
Shamira/
├── server.py              # FastAPI server with all endpoints
├── client.py              # Client library for node communication
├── utils.py               # Cryptographic utilities and finite field math
├── requirements.txt       # Python dependencies
├── pyproject.toml         # Project configuration
├── Dockerfile             # Docker container definition
├── initial_node_list.yaml # Cluster node configuration
├── tests/                 # Test suite
│   ├── test_server.py
│   ├── test_client.py
│   ├── test_cluster.py
│   └── test_utils.py
└── docker_compose/        # Docker Compose configuration
		└── docker-compose.yaml
```

### Code Formatting

The project uses Black and isort for code formatting:

```bash
# Format code
black .

# Sort imports
isort .
```

### Building Docker Image

```bash
./build_docker.sh
```

## Troubleshooting

### Common Issues

**Issue**: Tests hang or timeout
- **Solution**: Ensure all test database files are cleaned up between runs: `rm -f tests/data/*.db`

**Issue**: Node registration fails
- **Solution**: Check that the hostname is reachable and the node is running. Verify network configuration.

**Issue**: Incorrect computation results
- **Solution**: Verify all nodes completed their shard computations. Check logs for synchronization issues.

**Issue**: Authentication errors
- **Solution**: Ensure key pairs are properly generated and loaded. Check that public keys match between nodes.

### Logging

Shamira uses Python's logging module. Increase log level for debugging:

```python
logging.basicConfig(level=logging.DEBUG)
```

## Roadmap

Future enhancements planned:

- [ ] Full implementation of public variable type
- [ ] Beaver triple generation and management for efficient multiplication
- [ ] Support for more complex operations (division, comparison)
- [ ] Optimized polynomial evaluation algorithms
- [ ] WebSocket support for real-time node communication
- [ ] Blockchain integration layer
- [ ] Byzantine fault tolerance mechanisms
- [ ] Dynamic node addition/removal
- [ ] Performance benchmarking suite

## Contributing

Contributions are welcome! Please ensure:

1. All tests pass before submitting PR
2. Code is formatted with Black and isort
3. New features include appropriate tests
4. Documentation is updated for API changes

## License

[Specify your license here]

## References

- [Shamir's Secret Sharing](https://en.wikipedia.org/wiki/Shamir%27s_Secret_Sharing)
- [Secure Multi-Party Computation](https://en.wikipedia.org/wiki/Secure_multi-party_computation)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Elliptic Curve Cryptography](https://en.wikipedia.org/wiki/Elliptic-curve_cryptography)
