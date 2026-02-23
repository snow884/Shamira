import logging
import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import utils

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def requests_retry_session(
    retries=3,
    backoff_factor=0.3,
    status_forcelist=(500, 502, 503, 504),
    session=None,
):
    session = session or requests.Session()
    # Define the retry logic
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        # 'method_whitelist' has been deprecated in newer urllib3 versions.
        # Use 'allowed_methods' instead if on a modern urllib3 version (requests v2.25.0+).
        # By default, all methods except POST are retried.
    )
    # Mount the HTTPAdapter to all http:// and https:// prefixes
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class ShamiraClient:
    def __init__(
        self,
        node_host,
        current_node,
        get_session=None,
        keypair_file_path="key_pair.pem",
        private_key=None,
        public_key=None,
    ):
        logger.info("Initializing ShamiraClient for node host: %s", node_host)
        if get_session:
            self.session = get_session(node_host)
        else:
            self.session = requests_retry_session(retries=5, backoff_factor=2)

        self.node_host = node_host
        self.current_node = current_node

        self.keypair_file_path = (
            keypair_file_path or "key_pair.pem"
        )  # Replace with the actual file path

        if not (private_key and public_key):

            if os.path.isfile(self.keypair_file_path):
                self.private_key, self.public_key = utils.load_key_pair(
                    self.keypair_file_path
                )
                logger.info(
                    "Loaded existing key pair from '%s'.", self.keypair_file_path
                )
            else:
                logger.info("The file '%s' does not exist.", self.keypair_file_path)

                self.private_key, self.public_key = utils.generate_key_pair()

                utils.save_key_pair(
                    self.private_key, self.public_key, self.keypair_file_path
                )

                self.register_node(
                    hostname=current_node.hostname,
                    alias=current_node.alias,
                    description=current_node.description,
                )
        else:
            logger.info("Using provided key pair.")
            self.private_key = private_key
            self.public_key = public_key

        login_success = self.login_and_get_token()

        if not login_success:
            logger.info("Login failed for node host: %s. Registering node.", node_host)
            self.register_node(
                hostname=current_node.hostname,
                alias=current_node.alias,
                description=current_node.description,
            )
            logger.info("Node registered for node host: %s", node_host)
            self.login_and_get_token()
        else:
            logger.info("Login successful for node host: %s", node_host)

    def register_node(self, hostname, alias, description=""):
        logger.info(
            "Registering node %s with hostname: %s",
            self.current_node.hostname,
            hostname,
        )
        url = f"http://{self.node_host}/auth/register"
        pub_key_serialized = self.public_key.serialize()
        data = {
            "hostname": hostname,
            "alias": alias,
            "public_key": pub_key_serialized,
            "description": description,
        }

        response = self.session.post(url, json=data)

        response.raise_for_status()
        return response.json()

    def login_and_get_token(self):
        logger.info(
            "Logging in as node %s to node with hostname: %s",
            self.current_node.hostname,
            self.node_host,
        )
        url = f"http://{self.node_host}/auth/login"
        message = os.urandom(16)  # Random challenge message
        signature = utils.sign_message(self.private_key, message)
        pub_key_serialized = self.public_key.serialize()

        data = {
            "public_key": pub_key_serialized,
            "message": message.hex(),
            "signature": {"r": signature[0], "s": signature[1]},
        }
        response = self.session.post(url, json=data)

        response.raise_for_status()
        # print(response.json())
        if not response.json().get("token"):
            return False
        self.api_key = response.json()["token"]

        self.headers = {"Authorization": f"Bearer {self.api_key}"}

        self.ping()

        return True

    def ping(self):
        url = f"http://{self.node_host}/ping"

        response = self.session.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def get_nodes(self):
        url = f"http://{self.node_host}/nodes"
        response = self.session.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def get_jobs(self):
        url = f"http://{self.node_host}/jobs"
        response = self.session.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def create_job(self, job_obj):
        url = f"http://{self.node_host}/job"
        data = job_obj.model_dump()
        response = self.session.post(url, json=data, headers=self.headers)
        response.raise_for_status()
        return response.json()
