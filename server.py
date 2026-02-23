# Define the database URL for a local SQLite file named 'local_storage.db'
# '///' means relative path to the current directory
import copy
import enum
import json
import logging
import os
import random
from contextlib import asynccontextmanager
from time import time
from typing import List, Optional
from uuid import uuid4

import requests
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, model_validator
from sqlalchemy import Boolean, Column, Enum, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, relationship

import utils
from client import ShamiraClient
from utils import (
    EncryptedFiniteFieldElement,
    FiniteFieldElement,
    Point,
    PolynomialPoint,
    encrypt_message,
    get_shares,
    verify_signature,
)

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

SQLALCHEMY_DATABASE_URL = f"sqlite:///./tests/data/test_node_0.db"

# SQLALCHEMY_DATABASE_URL = "sqlite:///./local_storage.db"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)


def get_db_session():
    with Session(engine) as session:
        yield session


# Declare a base class for your models
Base = declarative_base()


class MynodeHealthStatusEnum(enum.Enum):
    healthy = "healthy"
    dead = "dead"
    error = "error"
    none = "none"


class Token(Base):
    __tablename__ = "token"
    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String, index=True)
    public_key = Column(String, index=True)
    expiration_time = Column(Integer, index=True)


class TokenSchema(BaseModel):
    token: str
    public_key: str
    expiration_time: int


class LoginCredentialsSchema(BaseModel):
    public_key: str
    message: str
    signature: dict


# Define your database model
class Node(Base):
    __tablename__ = "node"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hostname = Column(String, index=True)
    public_key = Column(String, index=True)
    alias = Column(String, index=True)
    is_current_node = Column(Boolean, default=False)
    description = Column(String, index=True)
    health_status = Column(
        Enum(MynodeHealthStatusEnum, name="mynodehealthstatusenum_type"),
        default=MynodeHealthStatusEnum.none,
    )


import hashlib
from typing import List

from pydantic import BaseModel, ConfigDict


# Pydantic V2 style
class NodeSchema(BaseModel):
    id: Optional[int] = None
    hostname: str
    alias: str
    is_current_node: Optional[bool] = False
    description: str
    health_status: Optional[MynodeHealthStatusEnum] = MynodeHealthStatusEnum.none
    public_key: str

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class VariableTypeEnum(enum.Enum):
    none = "none"
    shard = "shard"
    private_variable = "private_variable"
    vector_shard = "vector_shard"
    vector_private_variable = "vector_private_variable"


PARAM_LOOKUP = {
    "add": ["input_variable_name_1", "input_variable_name_2"],
    "multiply": ["input_variable_name_1", "input_variable_name_2"],
    "distribute_variable": ["variable_name"],
    "distribute_shards": ["variable_name"],
    "variable_to_shards": ["variable_name"],
    "shards_to_variable": ["variable_name"],
}


class MyStepTypeEnum(enum.Enum):
    init_variable = "init_variable"
    create_private_random_variable = "create_private_random_variable"
    assign_variable = "assign_variable"
    add = "add"
    multiply = "multiply"

    create_sharded_random_variable = "create_sharded_random_variable"
    apply_affine_transform = "apply_affine_transform"
    variable_to_shards = "variable_to_shards"
    shards_to_variable = "shards_to_variable"


class JobStatusEnum(enum.Enum):
    waiting = "waiting"
    started = "started"
    error = "error"
    completed = "completed"


class StepStatusEnum(enum.Enum):
    waiting = "waiting"
    started = "started"
    error = "error"
    completed = "completed"


class Program(Base):
    __tablename__ = "program"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, index=True)
    owner = Column(Integer, ForeignKey("node.id"))
    hash_id = Column(String, index=True, default="")
    signature = Column(String, index=True, default="")


class Job(Base):
    __tablename__ = "job"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, index=True)
    steps = relationship("Step", back_populates="job", cascade="all, delete-orphan")
    status = Column(
        Enum(JobStatusEnum, name="job_status_type"),
        index=True,
        default=JobStatusEnum.waiting,
    )
    expiration_time = Column(
        Integer, index=True, default=lambda: int(time()) + 60 * 60
    )  # default 60s from creation
    created_time = Column(Integer, index=True, default=lambda: int(time()))
    program_id = Column(Integer, ForeignKey("program.id"))

    hash_id = Column(String, index=True, default="")
    signature = Column(String, index=True, default="")

    def get_hash_id(self):
        return self.name + "_" + str(self.created_time)


class Step(Base):
    __tablename__ = "step"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_hash_id = Column(String, ForeignKey("job.hash_id"))
    status = Column(Enum(StepStatusEnum, name="step_status_type"), index=True)
    parameters = Column(String, index=True)
    assigned_node_public_key = Column(String, ForeignKey("node.public_key"))
    dest_node_public_key = Column(String, ForeignKey("node.public_key"))
    variable_name = Column(String, index=True)
    variable_type = Column(
        Enum(VariableTypeEnum, name="variable_type_enum"), index=True
    )
    output_value = Column(String, index=True)
    step_type = Column(Enum(MyStepTypeEnum, name="step_type"), index=True)

    hash_id = Column(String, index=True, default="")
    signature = Column(String, index=True, default="")

    job = relationship("Job", back_populates="steps")


class ProgramSchema(BaseModel):
    id: Optional[int] = None
    name: str
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


# Pydantic V2 style
class StepSchema(BaseModel):
    id: Optional[int] = None
    job_hash_id: Optional[str] = None
    status: Optional[StepStatusEnum] = StepStatusEnum.waiting
    parameters: Optional[str] = None
    dest_node_public_key: str
    assigned_node_public_key: str
    step_type: MyStepTypeEnum
    variable_type: VariableTypeEnum
    variable_name: str
    output_value: Optional[str] = None
    hash_id: Optional[str] = None
    signature: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    @model_validator(mode="after")
    def generate_hash(self) -> "StepSchema":
        # Generate hash based on name and data
        if not self.hash_id:
            self.hash_id = self.get_hash_id()
        return self

    def get_hash_id(self):
        return hashlib.sha256(
            (
                str(self.job_hash_id)
                + "_"
                + str(self.variable_name)
                + "_"
                + str(self.variable_type)
                + "_"
                + str(self.assigned_node_public_key)
                + "_"
                + str(self.dest_node_public_key)
                + "_"
                + str(self.step_type)
                + "_"
                + str(self.parameters)
            ).encode()
        ).hexdigest()


# Pydantic V2 style
class JobSchema(BaseModel):
    id: Optional[int] = None
    name: str
    steps: List[StepSchema] = []
    status: Optional[JobStatusEnum] = JobStatusEnum.waiting
    expiration_time: Optional[int] = None
    created_time: Optional[int] = None
    program_id: Optional[int] = None
    hash_id: Optional[str] = None
    signature: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class ShamiraTask:
    def __init__(
        self,
        db,
        name,
        value,
        variable_type: VariableTypeEnum,
        job=None,
        keypair_file_path: str = None,
        dest_node_public_key: str = None,
    ):

        self.db = db

        self._name = name
        self._variable_type = variable_type

        if isinstance(value, ShamiraTask):
            self._value = None
            self.job = copy.deepcopy(value.job)
            orig_var_name = value._name
            if variable_type == VariableTypeEnum.shard:
                nodes = self.db.query(Node).order_by(Node.id).all()
                self.merge_jobs(value)
                # If converting from private_variable, the assignment must happen on the node that has the private_variable
                if value._variable_type == VariableTypeEnum.private_variable:
                    assigned_node = value.job.steps[-1].dest_node_public_key
                    # for s in value.job.steps:
                    #     print(s.variable_name, s.variable_type, s.dest_node_public_key)
                    # if self._name == "Aa_sharded":
                    #     fsdggfdgfsdgfgfdsdfggsfd
                elif value._variable_type == VariableTypeEnum.shard:
                    # Allow shard-to-shard conversion
                    # The computation is already on shards, but we need to ensure the final result has the correct name
                    # Update the variable names in the last steps to match the new name
                    for step in self.job.steps:
                        if step.variable_name == orig_var_name:
                            step.variable_name = self._name
                    return
                else:
                    raise Exception(
                        f"Conversion from {value._variable_type} to shard is not"
                        " supported in the current implementation."
                    )

                for node in nodes:
                    step = StepSchema(
                        status="waiting",
                        step_type="assign_variable",
                        parameters='{"source_variable_name": "'
                        + orig_var_name
                        + '", "source_variable_type": "'
                        + value._variable_type.value
                        + '"}',
                        variable_name=self._name,
                        variable_type=self._variable_type,
                        assigned_node_public_key=assigned_node,
                        dest_node_public_key=node.public_key,
                        output_value=None,
                    )
                    self.job.steps.append(step)
                    step.hash_id = "hash_step_b_" + str(id(step))

            if variable_type == VariableTypeEnum.private_variable:
                if value._variable_type == VariableTypeEnum.private_variable:
                    self.merge_jobs(value)
                    step = StepSchema(
                        status="waiting",
                        step_type="assign_variable",
                        parameters='{"source_variable_name": "'
                        + orig_var_name
                        + '", "source_variable_type": "'
                        + value._variable_type.value
                        + '"}',
                        variable_name=self._name,
                        variable_type=self._variable_type,
                        assigned_node_public_key=get_current_node(db).public_key,
                        dest_node_public_key=get_current_node(db).public_key,
                        output_value=None,
                    )
                    self.job.steps.append(step)
                elif value._variable_type == VariableTypeEnum.shard:

                    nodes = self.db.query(Node).order_by(Node.id).all()
                    self.merge_jobs(value)
                    # switch dest public key on the previous stop to the current node

                    for s in self.job.steps:
                        print(s)

                        if (
                            s.variable_name == orig_var_name
                            and s.variable_type == "shard"
                        ):

                            s.dest_node_public_key = get_current_node(db).public_key

                    step = StepSchema(
                        status="waiting",
                        step_type="assign_variable",
                        parameters='{"source_variable_name": "'
                        + orig_var_name
                        + '", "source_variable_type": "'
                        + value._variable_type.value
                        + '"}',
                        variable_name=self._name,
                        variable_type=self._variable_type,
                        assigned_node_public_key=get_current_node(db).public_key,
                        dest_node_public_key=get_current_node(db).public_key,
                        output_value=None,
                    )
                    self.job.steps.append(step)
        elif isinstance(value, (str, int, list)):
            self._value = value

            if variable_type == VariableTypeEnum.shard:
                nodes = db.query(Node).order_by(Node.id).all()
                shares = get_shares(value, len(nodes), len(nodes))

                self.job = JobSchema(
                    name="Define variable " + self._name + " job",
                    status=JobStatusEnum.waiting,
                    steps=[],
                )

                for node, share in zip(nodes, shares):
                    output_value_x = encrypt_message(
                        Point(None, None).deserialize(node.public_key), share.x
                    )
                    output_value_y = encrypt_message(
                        Point(None, None).deserialize(node.public_key), share.y
                    )

                    step = StepSchema(
                        status="waiting",
                        step_type="init_variable",
                        variable_name=self._name,
                        variable_type=self._variable_type,
                        assigned_node_public_key=node.public_key,
                        dest_node_public_key=node.public_key,
                        output_value=json.dumps(
                            {
                                "x": output_value_x.serialize(),
                                "y": output_value_y.serialize(),
                            }
                        ),
                    )
                    self.job.steps.append(step)

            elif variable_type == VariableTypeEnum.private_variable:
                self.job = JobSchema(
                    name="Define variable " + self._name + " job",
                    status=JobStatusEnum.waiting,
                    steps=[],
                )

                encrypted_value = encrypt_message(
                    Point(None, None).deserialize(get_current_node(db).public_key),
                    self._value,
                )
                step = StepSchema(
                    status="waiting",
                    step_type=MyStepTypeEnum.init_variable,
                    variable_name=self._name,
                    variable_type=self._variable_type,
                    assigned_node_public_key=dest_node_public_key
                    if dest_node_public_key
                    else get_current_node(db).public_key,
                    dest_node_public_key=dest_node_public_key
                    if dest_node_public_key
                    else get_current_node(db).public_key,
                    output_value=encrypted_value.serialize(),
                )
                self.job.steps.append(step)

            if variable_type == VariableTypeEnum.vector_shard:
                self.job = JobSchema(
                    name="Define variable " + self._name + " job",
                    status=JobStatusEnum.waiting,
                    steps=[],
                )

                nodes = db.query(Node).all()
                shares = [
                    get_shares(value, len(nodes), len(nodes) - 2)
                    for value in self._value
                ]
                for node, vector_el_share in zip(nodes, shares):
                    output_value_x = [
                        encrypt_message(
                            Point(None, None).deserialize(node.public_key), v.x
                        )
                        for v in vector_el_share
                    ]
                    output_value_y = [
                        encrypt_message(
                            Point(None, None).deserialize(node.public_key), v.y
                        )
                        for v in vector_el_share
                    ]

                    step = StepSchema(
                        status="waiting",
                        step_type="init_variable",
                        variable_name=self._name,
                        variable_type=self._variable_type,
                        assigned_node_public_key=node.public_key,
                        dest_node_public_key=node.public_key,
                        output_value=json.dumps(
                            [
                                {"x": o_x, "y": o_y}
                                for o_x, o_y in zip(output_value_x, output_value_y)
                            ]
                        ),
                    )
                    self.job.steps.append(step)

            elif variable_type == VariableTypeEnum.vector_private_variable:
                encrypted_value = [
                    encrypt_message(get_current_node(db).public_key, v)
                    for v in self._value
                ]
                step = StepSchema(
                    status="waiting",
                    step_type="init_variable",
                    variable_name=self._name,
                    variable_type=self._variable_type,
                    assigned_node_public_key=get_current_node(db).public_key,
                    dest_node_public_key=get_current_node(db).public_key,
                    output_value=json.dumps(encrypted_value),
                )
                self.job.steps.append(step)

    def __add__(self, other):
        """Records an addition operation, lazy execution."""

        self.merge_jobs(other)

        if (
            self._variable_type == VariableTypeEnum.shard
            or other._variable_type == VariableTypeEnum.shard
        ):

            nodes = self.db.query(Node).all()
            for node in nodes:
                step = StepSchema(
                    status="waiting",
                    step_type="add",
                    parameters='{"variable_a": "'
                    + self._name
                    + '", "variable_b": "'
                    + other._name
                    + '"}',
                    variable_name="",
                    variable_type=VariableTypeEnum.shard,
                    assigned_node_public_key=node.public_key,
                    dest_node_public_key=node.public_key,
                    output_value=None,
                )
                self.job.steps.append(step)
                step.variable_name = (
                    "(" + self._name + ")" + " + " + "(" + other._name + ")"
                )
            self._name = "(" + self._name + ")" + " + " + "(" + other._name + ")"
        elif (self._variable_type == VariableTypeEnum.private_variable) and (
            other._variable_type == VariableTypeEnum.private_variable
        ):
            step = StepSchema(
                status="waiting",
                step_type="add",
                parameters='{"variable_a": "'
                + self._name
                + '", "variable_b": "'
                + other._name
                + '"}',
                variable_name="",
                variable_type=VariableTypeEnum.private_variable,
                assigned_node_public_key=self.job.steps[-1].dest_node_public_key,
                dest_node_public_key=self.job.steps[-1].dest_node_public_key,
                output_value=None,
            )
            self.job.steps.append(step)
            step.variable_name = (
                "(" + self._name + ")" + " + " + "(" + other._name + ")"
            )
            self._name = "(" + self._name + ")" + " + " + "(" + other._name + ")"
        return self

    def __sub__(self, other):

        self.merge_jobs(other)

        if (
            self._variable_type == VariableTypeEnum.shard
            or other._variable_type == VariableTypeEnum.shard
        ):

            nodes = self.db.query(Node).all()
            for node in nodes:
                step = StepSchema(
                    status="waiting",
                    step_type="add",
                    parameters='{"variable_a": "'
                    + self._name
                    + '", "variable_b": "'
                    + other._name
                    + '", "coef_1": 1, "coef_2": -1}',
                    variable_name="",
                    variable_type=self._variable_type,
                    assigned_node_public_key=node.public_key,
                    dest_node_public_key=node.public_key,
                    output_value=None,
                )
                self.job.steps.append(step)
                step.variable_name = (
                    "(" + self._name + ")" + " - " + "(" + other._name + ")"
                )
            self._name = "(" + self._name + ")" + " - " + "(" + other._name + ")"
        elif (self._variable_type == VariableTypeEnum.private_variable) and (
            other._variable_type == VariableTypeEnum.private_variable
        ):
            step = StepSchema(
                status="waiting",
                step_type="add",
                parameters='{"variable_a": "'
                + self._name
                + '", "variable_b": "'
                + other._name
                + '", "coef_1": 1, "coef_2": -1}',
                variable_name="",
                variable_type=self._variable_type,
                assigned_node_public_key=self.job.steps[-1].assigned_node_public_key,
                dest_node_public_key=self.job.steps[-1].assigned_node_public_key,
                output_value=None,
            )
            self.job.steps.append(step)
            step.variable_name = (
                "(" + self._name + ")" + " - " + "(" + other._name + ")"
            )
            self._name = "(" + self._name + ")" + " - " + "(" + other._name + ")"
        return self

    def __mul__(self, other):
        """Records a multiplication operation, lazy execution."""

        self.merge_jobs(other)

        if (
            self._variable_type == VariableTypeEnum.shard
            and other._variable_type == VariableTypeEnum.private_variable
        ):
            nodes = self.db.query(Node).all()
            for node in nodes:
                step = StepSchema(
                    status="waiting",
                    step_type="multiply",
                    parameters='{"variable_a": "'
                    + self._name
                    + '", "variable_b": "'
                    + other._name
                    + '"}',
                    variable_name="",
                    variable_type=self._variable_type,
                    assigned_node_public_key=node.public_key,
                    dest_node_public_key=node.public_key,
                    output_value=None,
                )
                self.job.steps.append(step)
                step.variable_name = (
                    "(" + self._name + ")" + " * " + "(" + other._name + ")"
                )
            self._name = "(" + self._name + ")" + " * " + "(" + other._name + ")"
            return self
        elif (
            self._variable_type == VariableTypeEnum.private_variable
            and other._variable_type == VariableTypeEnum.private_variable
        ):
            step = StepSchema(
                status="waiting",
                step_type="multiply",
                parameters='{"variable_a": "'
                + self._name
                + '", "variable_b": "'
                + other._name
                + '"}',
                variable_name="",
                variable_type=self._variable_type,
                assigned_node_public_key=self.job.steps[-1].assigned_node_public_key,
                dest_node_public_key=self.job.steps[-1].assigned_node_public_key,
                output_value=None,
            )
            self.job.steps.append(step)
            step.variable_name = (
                "(" + self._name + ")" + " * " + "(" + other._name + ")"
            )
            self._name = "(" + self._name + ")" + " * " + "(" + other._name + ")"
            return self

        elif (
            self._variable_type == VariableTypeEnum.private_variable
            and other._variable_type == VariableTypeEnum.shard
        ) or (
            self._variable_type == VariableTypeEnum.shard
            and other._variable_type == VariableTypeEnum.private_variable
        ):

            shard_var = self if self._variable_type == VariableTypeEnum.shard else other
            private_var = other if shard_var == self else self

            step = StepSchema(
                status="waiting",
                step_type="multiply",
                parameters='{"variable_a": "'
                + shard_var._name
                + '", "variable_b": "'
                + private_var._name
                + '"}',
                variable_name="",
                variable_type=VariableTypeEnum.shard,
                assigned_node_public_key=self.job.steps[-1].assigned_node_public_key,
                dest_node_public_key=self.job.steps[-1].assigned_node_public_key,
                output_value=None,
            )
            self.job.steps.append(step)
            step.variable_name = (
                "(" + self._name + ")" + " * " + "(" + other._name + ")"
            )
            self._name = "(" + self._name + ")" + " * " + "(" + other._name + ")"
            return self

        elif (
            self._variable_type == VariableTypeEnum.shard
            and other._variable_type == VariableTypeEnum.shard
        ):

            a, b, c = self.generate_beaver_tripple()

            delta = type(self)(self.db, f"delta", self - a, VariableTypeEnum.shard)
            epsilon = type(self)(self.db, f"epsilon", other - b, VariableTypeEnum.shard)

            delta_for_node = {}
            epsilon_for_node = {}

            for node in self.db.query(Node).all():

                delta_for_node[node.public_key] = type(self)(
                    self.db, f"delta", delta, VariableTypeEnum.private_variable
                )

                epsilon_for_node[node.public_key] = type(self)(
                    self.db, f"epsilon", epsilon, VariableTypeEnum.private_variable
                )

                z = type(self)(
                    self.db,
                    f"z",
                    c
                    + (a * epsilon_for_node[node.public_key])
                    + (b * delta_for_node[node.public_key])
                    + (
                        delta_for_node[node.public_key]
                        * epsilon_for_node[node.public_key]
                    ),
                    VariableTypeEnum.shard,
                )

                return z
        else:
            raise NotImplementedError(
                "Only multiplication of shard by non-shard or private variable by"
                " private variable is implemented."
            )

    def merge_jobs(self, other):

        unique_hash_ids = list(set(step.hash_id for step in self.job.steps))

        for step in other.job.steps:
            if step.hash_id not in unique_hash_ids:
                self.job.steps.append(copy.deepcopy(step))

        return self

    def generate_beaver_tripple(self):
        # generate beaver tripp
        """
        Alice:
        (ab*ba+r2), aa, ba and r1 -> cb = - r1 , d1 = ab*ba+r2,
        Bob:
        (aa*bb+r1), ab, bb and r2 -> ca = bb*aa+r1, da = - r2


        ca = (aa*ba) + (ab*ba+r2) -r1

        cb = ab*bb + (bb*aa+r1) -r2

        bb = ab*bb+  aa*ab+ba + ab*aa+bb

        (da+db) = aa*ab

        c = c1 + c2 = ab*aa

        (aa+ab)*(ba+bb) = aa*ba + aa*bb + ab*ba + ab*bb =
        """
        nodes = self.db.query(Node).all()

        alice_node = nodes[0]
        bob_node = nodes[1]

        Aa = ShamiraTask.generate_rand_variable(
            self.db,
            "Aa",
            VariableTypeEnum.private_variable,
            source_node_public_key=alice_node.public_key,
        )

        Ba = ShamiraTask.generate_rand_variable(
            self.db,
            "Ba",
            VariableTypeEnum.private_variable,
            source_node_public_key=alice_node.public_key,
        )

        R1 = ShamiraTask.generate_rand_variable(
            self.db,
            "R1",
            VariableTypeEnum.private_variable,
            source_node_public_key=alice_node.public_key,
        )

        Ab = ShamiraTask.generate_rand_variable(
            self.db,
            "Ab",
            VariableTypeEnum.private_variable,
            source_node_public_key=bob_node.public_key,
        )

        Bb = ShamiraTask.generate_rand_variable(
            self.db,
            "Bb",
            VariableTypeEnum.private_variable,
            source_node_public_key=bob_node.public_key,
        )

        R2 = ShamiraTask.generate_rand_variable(
            self.db,
            "R2",
            VariableTypeEnum.private_variable,
            source_node_public_key=bob_node.public_key,
        )

        # Bob computes Enc(Ba) * Ab + R2 for Alice (R2 is Bob's random value)
        affine_res_for_alice = Ba.compute_affine(
            Ab,
            R2,
            dest_node_public_key=alice_node.public_key,
            source_node_public_key=bob_node.public_key,
        )

        # Alice computes Enc(Bb) * Aa + R1 for Bob (R1 is Alice's random value)
        affine_res_for_bob = Bb.compute_affine(
            Aa,
            R1,
            dest_node_public_key=bob_node.public_key,
            source_node_public_key=alice_node.public_key,
        )

        # Alice computes: Aa*Ba + (Ba*Ab + R2) - R1 = Aa*Ba + Ba*Ab + R2 - R1
        Aa_for_Ca = copy.copy(Aa)
        Aa_for_Ca.db = self.db
        Aa_for_Ca.job = copy.deepcopy(Aa.job)
        Ba_for_Ca = copy.copy(Ba)
        Ba_for_Ca.db = self.db
        Ba_for_Ca.job = copy.deepcopy(Ba.job)
        Ca = (Aa_for_Ca * Ba_for_Ca) + affine_res_for_alice - R1

        # Bob computes: Ab*Bb + (Bb*Aa + R1) - R2 = Ab*Bb + Bb*Aa + R1 - R2
        Ab_for_Cb = copy.copy(Ab)
        Ab_for_Cb.db = self.db
        Ab_for_Cb.job = copy.deepcopy(Ab.job)
        Bb_for_Cb = copy.copy(Bb)
        Bb_for_Cb.db = self.db
        Bb_for_Cb.job = copy.deepcopy(Bb.job)
        Cb = (Ab_for_Cb * Bb_for_Cb) + affine_res_for_bob - R2

        Ca_sharded = ShamiraTask(self.db, "Ca_sharded", Ca, VariableTypeEnum.shard)

        Cb_sharded = ShamiraTask(self.db, "Cb_sharded", Cb, VariableTypeEnum.shard)

        Aa_sharded = ShamiraTask(self.db, "Aa_sharded", Aa, VariableTypeEnum.shard)

        Ab_sharded = ShamiraTask(self.db, "Ab_sharded", Ab, VariableTypeEnum.shard)

        Ba_sharded = ShamiraTask(self.db, "Ba_sharded", Ba, VariableTypeEnum.shard)

        Bb_sharded = ShamiraTask(self.db, "Bb_sharded", Bb, VariableTypeEnum.shard)

        A_sharded = Aa_sharded + Ab_sharded

        B_sharded = Ba_sharded + Bb_sharded

        C_sharded = Ca_sharded + Cb_sharded

        return A_sharded, B_sharded, C_sharded
        # return None, None, Aa

    def is_larger_than(self, other, true_branch, false_branch):
        new_ops = self._ops + [f"> {other}"]
        nodes = db.query(Node).all()
        for node in nodes:
            for node_other in nodes:
                step = StepSchema(
                    status="waiting",
                    step_type="random_shard",
                    parameters=None,
                    variable_name=node.public_key + "_random_shard",
                    variable_type=VariableTypeEnum.shard,
                    assigned_node_public_key=node.public_key,
                    dest_node_public_key=node_other.public_key,
                    output_value=None,
                )
            self.job.steps.append(step)

        for node in nodes:
            sum_r = ShamiraTask(
                None,
                node.public_key + "_random_shard_sum",
                VariableTypeEnum.shard,
                job=self.job,
            )
            self.job.steps.append(step)
            for node_other in nodes:
                step = StepSchema(
                    status="waiting",
                    step_type="add",
                    parameters='{"variable_a": "'
                    + sum_r._name
                    + '", "variable_b": "'
                    + node.public_key
                    + "_random_shard"
                    + '"}',
                    variable_name=node.public_key + "_random_shard_sum",
                    variable_type=VariableTypeEnum.shard,
                    assigned_node_public_key=node.public_key,
                    dest_node_public_key=node_other.public_key,
                    output_value=None,
                )
            self.job.steps.append(step)
            step.variable_name = "variable_" + step.hash_id

        step = StepSchema(
            status="waiting",
            step_type="multiply",
            parameters='{"variable_a": "'
            + self._name
            + '", "variable_b": "'
            + other._name
            + '"}',
            variable_name=self._name,
            variable_type=self._variable_type,
            assigned_node_public_key=get_current_node(db).public_key,
            dest_node_public_key=get_current_node(db).public_key,
            output_value=None,
        )
        self.job.steps.append(step)
        step.variable_name = "variable_" + step.hash_id

    def get_lineage(self):
        """Returns the chain of operations."""
        return " -> ".join(self._ops)

    @classmethod
    def generate_rand_variable(
        cls,
        db: Session,
        name: str,
        variable_type: VariableTypeEnum = VariableTypeEnum.private_variable,
        source_node_public_key: str = None,
        dest_node_public_key: str = None,
        keypair_file_path: str = None,
    ):
        """Creates a random variable and returns a ShamiraTask representing it."""

        self = cls.__new__(cls)

        self.db = db

        self._name = name
        self._variable_type = variable_type

        if self._variable_type == VariableTypeEnum.private_variable:

            job = JobSchema(
                name="Create random private variable " + name + " job",
                status=JobStatusEnum.waiting,
                steps=[
                    StepSchema(
                        status="waiting",
                        step_type=MyStepTypeEnum.create_private_random_variable,
                        variable_name=name,
                        variable_type=variable_type,
                        assigned_node_public_key=source_node_public_key,
                        dest_node_public_key=dest_node_public_key
                        if dest_node_public_key
                        else source_node_public_key,
                        output_value=None,
                    )
                ],
            )
            self.job = job
            return self
        else:

            raise NotImplementedError(
                "Only shard random variable creation is implemented."
            )

    def compute_affine(
        self,
        B,
        R,
        source_node_public_key: str = None,
        dest_node_public_key: str = None,
        keypair_file_path: str = None,
    ):

        new = copy.copy(self)
        new.db = self.db
        new._variable_type = VariableTypeEnum.private_variable
        new.job = JobSchema(
            name="Compute affine transformation of " + self._name + " job",
            status=JobStatusEnum.waiting,
            steps=[],
        )
        new.job.steps = copy.deepcopy(self.job.steps)

        new.merge_jobs(B)
        new.merge_jobs(R)

        step = StepSchema(
            status="waiting",
            step_type=MyStepTypeEnum.apply_affine_transform,
            variable_name=f"Enc({self._name})*{B._name} + {R._name}",
            variable_type=VariableTypeEnum.private_variable,
            assigned_node_public_key=source_node_public_key,
            dest_node_public_key=dest_node_public_key,
            parameters='{"x": "'
            + self._name
            + '", "coef": "'
            + B._name
            + '", "intercept": "'
            + R._name
            + '"}',
            output_value=None,
        )
        new.job.steps.append(step)
        new._name = f"Enc({self._name})*{B._name} + {R._name}"
        return new


"""
a = 3
b = 5
y = a + b
reveal y to node1

........

source: vector_open = (0,1,0,0)

dest: vector_open = (0,0,1,0)

amount: vector_open = 123


if sum(source) != 1:
    raise Exception("Source vector must sum to 1")
    
if sum(dest) != 1:
     

if sum( nodes * source ) == amount:
    acc2 = acc + amount * (source - dest)
    acc = acc2



sum(dest) = 1

sum(source*acc1) > amount

computation:

acc2 = acc + amount * (source - dest)

acc = acc2

"""


def variables_available(db: Session, step: Step) -> bool:
    variable_keys = {
        "variable_name_1",
        "variable_name_2",
        "variable_a",
        "variable_b",
        "variable_name_a",
        "variable_name_b",
        "source_variable_name",
        "coef",
        "intercept",
        "x",
    }

    params = json.loads(step.parameters) if step.parameters else {}
    variable_type = step.variable_type

    if step.step_type in [MyStepTypeEnum.apply_affine_transform]:
        for param_name, variable_name in params.items():
            last_step_reference = (
                db.query(Step)
                .filter(Step.variable_name == variable_name)
                .order_by(Step.id.desc())
                .first()
            )

            if not last_step_reference:
                logger.info(
                    "Uncompleted variable '%s' of type '%s' not found on node '%s'.",
                    variable_name,
                    variable_type,
                    get_current_node(db).hostname,
                )
                raise Exception(
                    f"Uncompleted variable '{variable_name}' of type '{variable_type}'"
                    f" not found on node '{get_current_node(db).hostname}'  while"
                    f" checking vars for step {step.variable_name}"
                )

            last_step_reference = (
                db.query(Step)
                .filter(
                    Step.variable_name == variable_name,
                    Step.status == StepStatusEnum.completed,
                )
                .order_by(Step.id.desc())
                .first()
            )

            if not last_step_reference:
                logger.info(
                    "Variable '%s' of type '%s' not found on node '%s'.",
                    variable_name,
                    variable_type,
                    get_current_node(db).hostname,
                )
                return False

    if step.step_type in [
        MyStepTypeEnum.add,
        MyStepTypeEnum.multiply,
        MyStepTypeEnum.shards_to_variable,
        MyStepTypeEnum.variable_to_shards,
    ]:

        for param_name, variable_name in params.items():
            if param_name not in variable_keys:
                continue
            if not variable_name:
                continue

            last_step_reference = (
                db.query(Step)
                .filter(
                    Step.variable_name == variable_name,
                    Step.dest_node_public_key == get_current_node(db).public_key,
                )
                .order_by(Step.id.desc())
                .first()
            )

            if not last_step_reference:
                logger.info(
                    "Uncompleted variable '%s' of type '%s' not found on node '%s'.",
                    variable_name,
                    variable_type,
                    get_current_node(db).hostname,
                )
                raise Exception(
                    f"Uncompleted variable '{variable_name}' of type '{variable_type}'"
                    f" not found on node '{get_current_node(db).hostname}' while"
                    f" checking vars for step {step.variable_name}."
                )

            last_step_reference = (
                db.query(Step)
                .filter(
                    Step.variable_name == variable_name,
                    Step.dest_node_public_key == get_current_node(db).public_key,
                    Step.status == StepStatusEnum.completed,
                )
                .order_by(Step.id.desc())
                .first()
            )

            if not last_step_reference:
                logger.info(
                    "Variable '%s' of type '%s' not found on node '%s'.",
                    variable_name,
                    variable_type,
                    get_current_node(db).hostname,
                )
                return False

            logger.info(
                "Variable '%s' of type '%s' was found on node '%s'.",
                variable_name,
                variable_type,
                get_current_node(db).hostname,
            )

    if step.step_type in [MyStepTypeEnum.assign_variable]:
        source_variable_name = params.get("source_variable_name")
        source_variable_type = params.get("source_variable_type")

        last_step_reference = (
            db.query(Step)
            .filter(
                Step.variable_name == source_variable_name,
                Step.variable_type == source_variable_type,
                Step.dest_node_public_key == get_current_node(db).public_key,
            )
            .order_by(Step.id.desc())
            .first()
        )

        if not last_step_reference:
            logger.info(
                "Uncompleted variable '%s' of type '%s' not found on node '%s'.",
                source_variable_name,
                source_variable_type,
                get_current_node(db).hostname,
            )
            raise Exception(
                f"Uncompleted variable '{source_variable_name}' of type"
                f" '{source_variable_type}' not found on node"
                f" '{get_current_node(db).hostname}' while checking vars for step"
                f" {step.variable_name}."
            )

        last_step_reference = (
            db.query(Step)
            .filter(
                Step.variable_name == source_variable_name,
                Step.variable_type == source_variable_type,
                Step.dest_node_public_key == get_current_node(db).public_key,
                Step.status == StepStatusEnum.completed,
            )
            .order_by(Step.id.desc())
            .first()
        )

        if not last_step_reference:
            logger.info(
                "Variable '%s' of type '%s' not found on node '%s'.",
                source_variable_name,
                source_variable_type,
                get_current_node(db).hostname,
            )
            return False

    return True


def get_variable(
    db: Session,
    variable_name: str,
    variable_type: VariableTypeEnum,
    keypair_file_path: str = None,
) -> str:

    logger.info(
        "Looking for variable '%s' of type '%s' on node '%s'.",
        variable_name,
        variable_type,
        get_current_node(db).hostname,
    )

    last_step_reference = (
        db.query(Step)
        .filter(
            Step.variable_name == variable_name,
            Step.variable_type == variable_type,
            Step.dest_node_public_key == get_current_node(db).public_key,
            Step.status == StepStatusEnum.completed,
        )
        .order_by(Step.id.desc())
        .first()
    )

    if not last_step_reference:

        raise Exception(
            f"Variable '{variable_name}' of type '{variable_type}' not found on this"
            " node."
        )

    public_key, private_key = generate_keypair(keypair_file_path=keypair_file_path)

    if last_step_reference.variable_type == VariableTypeEnum.private_variable:
        encrypted_element = EncryptedFiniteFieldElement(None, None).deserialize(
            last_step_reference.output_value
        )
        variable_value = encrypted_element.decrypt(private_key)
    elif last_step_reference.variable_type == VariableTypeEnum.shard:
        steps = (
            db.query(Step)
            .filter(
                Step.variable_name == variable_name,
                Step.variable_type == variable_type,
                Step.dest_node_public_key == get_current_node(db).public_key,
            )
            .all()
        )
        if len(steps) == 1:
            try:
                output_x_encrypted = EncryptedFiniteFieldElement(
                    None, None
                ).deserialize(json.loads(last_step_reference.output_value)["x"])
                output_y_encrypted = EncryptedFiniteFieldElement(
                    None, None
                ).deserialize(json.loads(last_step_reference.output_value)["y"])
            except (KeyError, json.JSONDecodeError) as e:
                logger.error(
                    f"Failed to parse output_value for shard {variable_name}."
                    f" output_value={last_step_reference.output_value}, error={e}"
                )
                raise
            output_x = output_x_encrypted.decrypt(private_key)
            output_y = output_y_encrypted.decrypt(private_key)

            pol = PolynomialPoint(output_x, output_y)
            return pol
        elif len(steps) > 1:
            variable_values = []

            for step in steps:
                try:
                    output_x_encrypted = EncryptedFiniteFieldElement(
                        None, None
                    ).deserialize(json.loads(step.output_value)["x"])
                    output_y_encrypted = EncryptedFiniteFieldElement(
                        None, None
                    ).deserialize(json.loads(step.output_value)["y"])
                except (KeyError, json.JSONDecodeError) as e:
                    logger.error(
                        f"Failed to parse output_value for shard {variable_name}."
                        f" output_value={step.output_value}, error={e}"
                    )
                    raise
                output_x = output_x_encrypted.decrypt(private_key)
                output_y = output_y_encrypted.decrypt(private_key)

                pol = PolynomialPoint(output_x, output_y)

                variable_values.append(pol)

            reconstructed_point = utils.lagrange_interpolation(
                variable_values, FiniteFieldElement(0, utils.N)
            )

            variable_value = reconstructed_point.value

        elif len(steps) == 0:
            raise Exception(
                f"No shards found for variable {variable_name} on this node."
            )

    return variable_value


def set_variable(
    db: Session,
    step: Step,
    variable_name: str,
    variable_type: VariableTypeEnum,
    value: any,
) -> None:
    current_step_reference = (
        db.query(Step)
        .filter(
            Step.id == step.id,
            Step.variable_type == variable_type,
            Step.node_public_key == get_current_node(db).public_key,
            Step.variable_name == variable_name,
        )
        .first()
    )

    if variable_type == VariableTypeEnum.private_variable:
        encrypted_value = utils.encrypt_message(value)
        current_step_reference.output_value = encrypted_value
        current_step_reference.status = StepStatusEnum.completed
        db.commit()
    elif variable_type == VariableTypeEnum.shard:
        # For shards, we assume 'value' is a list of shard values
        steps = db.query(Step).filter(
            Step.id == step.id,
            Step.variable_type == variable_type,
            Step.node_public_key == get_current_node(db).public_key,
            Step.variable_name == variable_name,
        )

        if len(steps) == 1:

            output_value_x = encrypt_message(
                Point(None, None).deserialize(
                    current_step_reference.dest_node_public_key
                ),
                value.x,
            )
            output_value_y = encrypt_message(
                Point(None, None).deserialize(
                    current_step_reference.dest_node_public_key
                ),
                value.y,
            )

            current_step_reference.output_value = json.dumps(
                {"x": output_value_x.serialize(), "y": output_value_y.serialize()}
            )
            current_step_reference.status = StepStatusEnum.completed
            db.commit()

        elif len(steps) > 1:

            shares = get_shares(value, len(steps), len(steps) - 2)

            for share, step in zip(shares, steps):
                output_value_x = encrypt_message(
                    Point(None, None).deserialize(step.dest_node_public_key), share.x
                )
                output_value_y = encrypt_message(
                    Point(None, None).deserialize(step.dest_node_public_key), share.y
                )

                step.output_value = json.dumps(
                    {"x": output_value_x.serialize(), "y": output_value_y.serialize()}
                )
                step.status = StepStatusEnum.completed

            db.commit()
        else:
            raise Exception(
                f"No steps found for variable {variable_name} on this node to set the"
                " value."
            )


def init_all_tables():
    Base.metadata.create_all(engine)


def add_current_node(
    db: Session, hostname: str, alias: str, description: str, public_key: str
):

    new_node = Node(
        hostname=hostname,
        alias=alias,
        description=description,
        public_key=public_key,
        is_current_node=True,
        health_status=MynodeHealthStatusEnum.healthy,
    )
    db.add(new_node)
    db.commit()
    db.refresh(new_node)
    return new_node


def get_current_node(db: Session) -> Node:
    current_node = db.query(Node).filter(Node.is_current_node == True).first()
    if not current_node:
        raise Exception("Current node not found in the database.")
    return current_node


def check_node_network(db: Session):

    nodes = db.query(Node).all()

    for node in nodes:
        logger.info(f"Node: {node}")
        try:
            res = ShamiraClient(
                node_host=node.hostname, api_key="your_api_key_here"
            ).ping()

        except requests.exceptions.RequestException as e:
            print(f"Error contacting node {node.hostname}: {e}")
            node.health_status = MynodeHealthStatusEnum.dead
            db.commit()
            continue

        logger.info(f"Response from node {node.hostname}: {res}")

        node.health_status = MynodeHealthStatusEnum.healthy
        db.commit()


def generate_keypair(keypair_file_path=None):

    keypair_file_path = keypair_file_path or "key_pair.pem"

    if os.path.isfile(keypair_file_path):
        private_key, public_key = utils.load_key_pair(keypair_file_path)

    else:
        print(f"The file '{keypair_file_path}' does not exist.")

        private_key, public_key = utils.generate_key_pair()

        utils.save_key_pair(private_key, public_key, keypair_file_path)

    return public_key, private_key


def load_initial_nodes(
    db: Session, initial_nodes_override=None, keypair_file_path=None
):
    nodes = db.query(Node).all()
    if len(nodes) == 0:
        initial_nodes = (
            initial_nodes_override
            or yaml.load(open("initial_node_list.yaml"), Loader=yaml.FullLoader)[
                "nodes"
            ]
        )
        for node_data in initial_nodes:
            if node_data.get("is_current_node", False) and not node_data.get(
                "public_key"
            ):
                pub_key, _ = generate_keypair(keypair_file_path=keypair_file_path)
                node_data["public_key"] = pub_key.serialize()

            node = Node(
                hostname=node_data["hostname"],
                alias=node_data["alias"],
                description=node_data["description"],
                health_status=MynodeHealthStatusEnum.healthy,
                is_current_node=node_data.get("is_current_node", False),
                public_key=node_data.get("public_key", ""),
            )
            db.add(node)
            print(f"Added initial node: {node_data['hostname']}")
        db.commit()
        print("Initial nodes loaded into the database.")


def propagate_nodes(db: Session, get_client_session=None, keypair_file_path=None):
    logger.info("Propagating nodes...")
    nodes = db.query(Node).filter(Node.is_current_node == False).all()
    logger.info("Nodes to propagate to: %s", [node.hostname for node in nodes])

    for node in nodes:
        logger.info("Propagating nodes from node: %s", node.hostname)
        remote_nodes = ShamiraClient(
            node_host=node.hostname,
            get_session=get_client_session,
            current_node=get_current_node(db),
            keypair_file_path=keypair_file_path,
        ).get_nodes()
        logger.info(
            "Remote nodes from %s received: %s",
            node.hostname,
            [node["hostname"] for node in remote_nodes],
        )
        for remote_node in remote_nodes:
            # logger.info("Processing remote node: %s", remote_node["hostname"])
            existing_node = (
                db.query(Node).filter(Node.hostname == remote_node["hostname"]).first()
            )
            if not existing_node:
                logger.info("Adding new node from remote: %s", remote_node["hostname"])
                new_node = Node(
                    hostname=remote_node["hostname"],
                    alias=remote_node["alias"],
                    description=remote_node.get("description", ""),
                    health_status=remote_node.get("health_status", ""),
                    public_key=remote_node.get("public_key", ""),
                    is_current_node=False,
                )
                db.add(new_node)
            else:
                if not existing_node.is_current_node:

                    logger.info(
                        "Updating existing node from remote: %s",
                        remote_node["hostname"],
                    )
                    existing_node.alias = remote_node["alias"]
                    existing_node.description = remote_node.get(
                        "description", existing_node.description
                    )
                    existing_node.health_status = remote_node.get(
                        "health_status", existing_node.health_status
                    )
                    existing_node.public_key = remote_node.get(
                        "public_key", existing_node.public_key
                    )
                else:
                    logger.info(
                        "Current node found on remote: %s", remote_node["hostname"]
                    )
                    if (
                        existing_node.hostname != remote_node["hostname"]
                        or existing_node.alias != remote_node["alias"]
                        or existing_node.description != remote_node["description"]
                        or existing_node.description != remote_node["description"]
                        or (not remote_node["public_key"])
                    ):
                        logger.info(
                            "Updating current node on remote: %s",
                            remote_node["hostname"],
                        )
                        ShamiraClient(
                            node_host=node.hostname,
                            get_session=get_client_session,
                            current_node=get_current_node(db),
                            keypair_file_path=keypair_file_path,
                        ).register_node(
                            existing_node.hostname,
                            existing_node.alias,
                            existing_node.description,
                        )

        db.commit()
        print("Initial nodes loaded into the database.")


def start_stop_jobs(db: Session, get_client_session=None, keypair_file_path=None):
    current_node = get_current_node(db)
    jobs = db.query(Job).filter(Job.status == JobStatusEnum.started).all()

    steps_completed = 0
    steps_total = 0

    if not jobs:
        next_job = (
            db.query(Job)
            .filter(Job.status == JobStatusEnum.waiting)
            .order_by(Job.created_time.asc())
            .first()
        )

        if next_job:
            next_job.status = JobStatusEnum.started
            next_job.expiration_time = int(time()) + 60 * 60

            db.commit()
    else:
        for job in jobs:

            if job.expiration_time < int(time()):
                job.status = JobStatusEnum.error
                db.commit()
                continue

            for step in job.steps:

                if step.status == StepStatusEnum.completed:
                    steps_completed += 1
                steps_total += 1

            if steps_completed == steps_total:
                job.status = JobStatusEnum.completed
                db.commit()


def execute_jobs(db: Session, get_client_session=None, keypair_file_path=None):

    jobs = db.query(Job).filter(Job.status == JobStatusEnum.started).all()
    logger.info(f"Executing jobs... Jobs in progress: {len(jobs)}")

    for job in jobs:
        steps = (
            db.query(Step)
            .filter(
                Step.job_hash_id == job.hash_id,
                Step.status == StepStatusEnum.waiting,
                Step.assigned_node_public_key == get_current_node(db).public_key,
            )
            .order_by(Step.id.asc())
            .all()
        )
        current_step = None
        for step_i in steps:
            logger.info(
                f"Step: {step_i.id}, Job Hash: {step_i.job_hash_id}, Type:"
                f" {step_i.step_type}, Status: {step_i.status}, Parameters:"
                f" {step_i.parameters}"
            )
            if step_i.step_type in [
                MyStepTypeEnum.init_variable,
                MyStepTypeEnum.create_private_random_variable,
            ]:
                logger.info(
                    f"Init variable step {step_i.id} found, ready for execution."
                )
                current_step = step_i
                break

            params = json.loads(step_i.parameters or "{}")
            if not variables_available(db, step_i):
                logger.info(
                    f"Variables not available for step {step_i.id}, skipping execution"
                    " for now."
                )
                continue

            logger.info(
                f"Variables available for step {step_i.id}, ready for execution."
            )
            current_step = step_i
            break

        try:
            if current_step is not None:

                step = current_step

                if step.step_type == MyStepTypeEnum.init_variable:

                    step.status = StepStatusEnum.completed

                if step.step_type == MyStepTypeEnum.create_private_random_variable:

                    random_value = random.randint(1, 100)

                    encrypted_output_value = encrypt_message(
                        Point(None, None).deserialize(step.dest_node_public_key),
                        random_value,
                    )

                    step.output_value = encrypted_output_value.serialize()

                    step.status = StepStatusEnum.completed

                if step.step_type == MyStepTypeEnum.add:

                    params = json.loads(step.parameters) if step.parameters else {}
                    variable_name_1 = (
                        params.get("variable_name_1")
                        or params.get("variable_a")
                        or params.get("variable_name_a")
                    )
                    variable_name_2 = (
                        params.get("variable_name_2")
                        or params.get("variable_b")
                        or params.get("variable_name_b")
                    )
                    coef_1 = params.get("coef_1") or 1
                    coef_2 = params.get("coef_2") or 1

                    if step.variable_type == VariableTypeEnum.shard:
                        if not variables_available(db, step):
                            logger.info(
                                f"Variables not available for step {step.id}, skipping"
                                " execution for now."
                            )
                            return

                        output_value = (
                            get_variable(
                                db,
                                variable_name_1,
                                VariableTypeEnum.shard,
                                keypair_file_path=keypair_file_path,
                            )
                            * coef_1
                            + get_variable(
                                db,
                                variable_name_2,
                                VariableTypeEnum.shard,
                                keypair_file_path=keypair_file_path,
                            )
                            * coef_2
                        )

                        output_value_x = encrypt_message(
                            Point(None, None).deserialize(step.dest_node_public_key),
                            output_value.x,
                        )
                        output_value_y = encrypt_message(
                            Point(None, None).deserialize(step.dest_node_public_key),
                            output_value.y,
                        )

                        step.output_value = json.dumps(
                            {
                                "x": output_value_x.serialize(),
                                "y": output_value_y.serialize(),
                            }
                        )

                        step.status = StepStatusEnum.completed
                    elif step.variable_type == VariableTypeEnum.private_variable:
                        if not variables_available(db, step):
                            logger.info(
                                f"Variables not available for step {step.id}, skipping"
                                " execution for now."
                            )
                            return
                        output_value = (
                            get_variable(
                                db,
                                variable_name_1,
                                VariableTypeEnum.private_variable,
                                keypair_file_path=keypair_file_path,
                            )
                            * coef_1
                            + get_variable(
                                db,
                                variable_name_2,
                                VariableTypeEnum.private_variable,
                                keypair_file_path=keypair_file_path,
                            )
                            * coef_2
                        )

                        encrypted_output_value = encrypt_message(
                            Point(None, None).deserialize(step.dest_node_public_key),
                            output_value,
                        )

                        step.output_value = encrypted_output_value.serialize()
                        step.status = StepStatusEnum.completed

                if step.step_type == MyStepTypeEnum.multiply:

                    params = json.loads(step.parameters) if step.parameters else {}
                    variable_name_1 = (
                        params.get("variable_name_1")
                        or params.get("variable_a")
                        or params.get("variable_name_a")
                    )
                    variable_name_2 = (
                        params.get("variable_name_2")
                        or params.get("variable_b")
                        or params.get("variable_name_b")
                    )

                    if step.variable_type == VariableTypeEnum.shard:

                        if not variables_available(db, step):
                            logger.info(
                                f"Variables not available for step {step.id}, skipping"
                                " execution for now."
                            )
                            return
                        output_value = get_variable(
                            db,
                            variable_name_1,
                            VariableTypeEnum.shard,
                            keypair_file_path=keypair_file_path,
                        ) * get_variable(
                            db,
                            variable_name_2,
                            VariableTypeEnum.shard,
                            keypair_file_path=keypair_file_path,
                        )

                        output_value_x = encrypt_message(
                            Point(None, None).deserialize(step.dest_node_public_key),
                            output_value.x,
                        )
                        output_value_y = encrypt_message(
                            Point(None, None).deserialize(step.dest_node_public_key),
                            output_value.y,
                        )

                        step.output_value = json.dumps(
                            {
                                "x": output_value_x.serialize(),
                                "y": output_value_y.serialize(),
                            }
                        )

                        step.status = StepStatusEnum.completed

                    elif step.variable_type == VariableTypeEnum.private_variable:
                        if not variables_available(db, step):
                            logger.info(
                                f"Variables not available for step {step.id}, skipping"
                                " execution for now."
                            )
                            return
                        output_value = get_variable(
                            db,
                            variable_name_1,
                            VariableTypeEnum.private_variable,
                            keypair_file_path=keypair_file_path,
                        ) * get_variable(
                            db,
                            variable_name_2,
                            VariableTypeEnum.private_variable,
                            keypair_file_path=keypair_file_path,
                        )

                        encrypted_output_value = encrypt_message(
                            Point(None, None).deserialize(step.dest_node_public_key),
                            output_value,
                        )

                        step.output_value = encrypted_output_value.serialize()
                        step.status = StepStatusEnum.completed

                if step.step_type == MyStepTypeEnum.assign_variable:

                    # Find all assign_variable steps for this variable
                    all_assign_steps = (
                        db.query(Step)
                        .filter(
                            Step.variable_name == step.variable_name,
                            Step.step_type == MyStepTypeEnum.assign_variable,
                            Step.variable_type == step.variable_type,
                        )
                        .order_by(Step.id.asc())
                        .all()
                    )

                    if len(all_assign_steps) == 1:

                        if step.variable_type == VariableTypeEnum.private_variable:

                            params = (
                                json.loads(step.parameters) if step.parameters else {}
                            )
                            variable_name = params.get("source_variable_name")
                            source_variable_type_str = params.get(
                                "source_variable_type", "private_variable"
                            )
                            source_variable_type = VariableTypeEnum(
                                source_variable_type_str
                            )

                            if not variables_available(db, step):
                                logger.info(
                                    f"Variables not available for step {step.id},"
                                    " skipping execution for now."
                                )
                                return
                            output_value = get_variable(
                                db,
                                variable_name,
                                source_variable_type,
                                keypair_file_path=keypair_file_path,
                            )

                            encrypted_output_value = encrypt_message(
                                Point(None, None).deserialize(
                                    step.dest_node_public_key
                                ),
                                output_value,
                            )

                            step.output_value = encrypted_output_value.serialize()
                        elif step.variable_type == VariableTypeEnum.shard:

                            params = (
                                json.loads(step.parameters) if step.parameters else {}
                            )
                            variable_name = params.get("source_variable_name")
                            source_variable_type_str = params.get(
                                "source_variable_type", "shard"
                            )
                            source_variable_type = VariableTypeEnum(
                                source_variable_type_str
                            )

                            if not variables_available(db, step):
                                logger.info(
                                    f"Variables not available for step {step.id},"
                                    " skipping execution for now."
                                )
                                return
                            output_value = get_variable(
                                db,
                                variable_name,
                                source_variable_type,
                                keypair_file_path=keypair_file_path,
                            )

                            output_value_x = encrypt_message(
                                Point(None, None).deserialize(
                                    step.dest_node_public_key
                                ),
                                output_value.x,
                            )
                            output_value_y = encrypt_message(
                                Point(None, None).deserialize(
                                    step.dest_node_public_key
                                ),
                                output_value.y,
                            )

                            step.output_value = json.dumps(
                                {
                                    "x": output_value_x.serialize(),
                                    "y": output_value_y.serialize(),
                                }
                            )

                            step.status = StepStatusEnum.completed

                    if len(all_assign_steps) > 1:
                        if step.variable_type == VariableTypeEnum.shard:
                            params = (
                                json.loads(step.parameters) if step.parameters else {}
                            )
                            variable_name = params.get("source_variable_name")
                            source_variable_type_str = params.get(
                                "source_variable_type", "shard"
                            )
                            source_variable_type = VariableTypeEnum(
                                source_variable_type_str
                            )
                            if not variables_available(db, step):
                                logger.info(
                                    f"Variables not available for step {step.id},"
                                    " skipping execution for now."
                                )
                                return
                            output_value = get_variable(
                                db,
                                variable_name,
                                source_variable_type,
                                keypair_file_path=keypair_file_path,
                            )

                            # get_shares expects an integer, not a FiniteFieldElement
                            if isinstance(output_value, FiniteFieldElement):
                                output_value = output_value.value

                            shards = get_shares(
                                output_value,
                                n_shares=len(all_assign_steps),
                                k_threshold=len(all_assign_steps),
                            )

                            vars_not_ready = False

                            if vars_not_ready:
                                continue

                            for share, step_i in zip(shards, all_assign_steps):

                                output_value_x = encrypt_message(
                                    Point(None, None).deserialize(
                                        step_i.dest_node_public_key
                                    ),
                                    share.x,
                                )
                                output_value_y = encrypt_message(
                                    Point(None, None).deserialize(
                                        step_i.dest_node_public_key
                                    ),
                                    share.y,
                                )

                                step_i.output_value = json.dumps(
                                    {
                                        "x": output_value_x.serialize(),
                                        "y": output_value_y.serialize(),
                                    }
                                )

                                step_i.status = StepStatusEnum.completed
                        elif step.variable_type == VariableTypeEnum.private_variable:
                            params = (
                                json.loads(step.parameters) if step.parameters else {}
                            )
                            variable_name = params.get("source_variable_name")
                            source_variable_type_str = params.get(
                                "source_variable_type", "private_variable"
                            )
                            source_variable_type = VariableTypeEnum(
                                source_variable_type_str
                            )
                            if not variables_available(db, step):
                                logger.info(
                                    f"Variables not available for step {step.id},"
                                    " skipping execution for now."
                                )
                                return
                            output_value = get_variable(
                                db,
                                variable_name,
                                source_variable_type,
                                keypair_file_path=keypair_file_path,
                            )

                            encrypted_output_value = encrypt_message(
                                Point(None, None).deserialize(
                                    step.dest_node_public_key
                                ),
                                output_value,
                            )
                            step.output_value = encrypted_output_value.serialize()
                            step.status = StepStatusEnum.completed
                        else:
                            raise Exception(
                                "Multiple steps found for the same variable but the"
                                " variable type is not shard. This is inconsistent."
                            )

                if step.step_type == MyStepTypeEnum.apply_affine_transform:

                    if not variables_available(db, step):
                        logger.info(
                            f"Variables not available for step {step.id}, skipping"
                            " execution for now."
                        )
                        return

                    params = json.loads(step.parameters) if step.parameters else {}

                    coef = get_variable(
                        db,
                        params.get("coef"),
                        VariableTypeEnum.private_variable,
                        keypair_file_path=keypair_file_path,
                    )
                    intercept = get_variable(
                        db,
                        params.get("intercept"),
                        VariableTypeEnum.private_variable,
                        keypair_file_path=keypair_file_path,
                    )
                    # x = get_variable(db, params.get("x"), VariableTypeEnum.private_variable, keypair_file_path=keypair_file_path)
                    logger.info(
                        f"Applying affine transform with coef: {coef}, intercept:"
                        f" {intercept} for step {step.id}"
                    )
                    step_reference = (
                        db.query(Step)
                        .filter(
                            Step.variable_name == params.get("x"),
                            Step.variable_type == VariableTypeEnum.private_variable,
                        )
                        .order_by(Step.id.desc())
                        .first()
                    )
                    if step_reference.status != StepStatusEnum.completed:
                        logger.info(
                            f"Variable 'x' not ready for step {step.id}, skipping"
                            " execution for now."
                        )
                        return
                    encrypted_x = EncryptedFiniteFieldElement(None, None).deserialize(
                        step_reference.output_value
                    )
                    # encrypted_coef = utils.encrypt_message(Point(None,None).deserialize(step.dest_node_public_key), coef)
                    encrypted_intercept = utils.encrypt_message(
                        Point(None, None).deserialize(step.dest_node_public_key),
                        intercept,
                    )

                    output_value = encrypted_x**coef * encrypted_intercept

                    step.output_value = output_value.serialize()

                    step.status = StepStatusEnum.completed

            db.commit()
        except Exception as e:
            logger.error(
                "Error executing step"
                f" {step.id} {step.variable_name} ({step.step_type}) of job"
                f" {job.hash_id}: {str(e)}"
            )
            raise Exception(
                "Error executing step"
                f" {step.id} {step.variable_name} ({step.step_type}) of job"
                f" {job.hash_id}: {str(e)}"
            )

            # elif step.step_type == MyStepTypeEnum.distribute_shards:

            #     pass

            # elif step.step_type == MyStepTypeEnum.distribute_private_variable:

            # elif step.step_type == MyStepTypeEnum.variable_to_shards:

            #     # find last reference to the variable
            #     last_step_reference = db.query(Step).filter(Step.id < step.id, Step.status == StepStatusEnum.completed, Step.variable_type == VariableTypeEnum.variable, Step.dest_node_private_key==get_current_node(db).private_key, Step.variable_name==step.parameters.get("variable_name") ).order_by(Step.id.desc()).first() if step else None

            #     variable_value = utils.decrypt_message(last_step_reference.encrypted_value)
            #     variable_value_ff = FiniteFieldElement(variable_value, prime=utils.PRIME)

            #     private_key = generate_keypair(keypair_file_path=None)

            #     decrypted_var = utils.decrypt_message(private_key, last_step_reference.encrypted_value)

            #     shards = utils.split_variable_into_shards(decrypted_var, num_shards=len(step.))

            # elif step.step_type == MyStepTypeEnum.shards_to_variable:

            #     last_step_reference = db.query(Step).filter(Step.id < step.id, Step.status == StepStatusEnum.completed, Step.variable_type == VariableTypeEnum.variable, Step.dest_node_private_key==get_current_node(db).private_key, Step.variable_name==step.parameters.get("variable_name") ).order_by(Step.id.desc()).first() if step else None

            # if step.status == StepStatusEnum.waiting:
            #     # Execute the step
            #     step.status = StepStatusEnum.started
            #     db.commit()

            #     db.query(Step).filter(Step.id == step.id).first()

            #     # After execution, update the step status
            #     step.status = StepStatusEnum.completed
            #     db.commit()


def propagate_jobs(db: Session, get_client_session=None, keypair_file_path=None):
    nodes = db.query(Node).filter(Node.is_current_node == False).all()
    logger.info("Propagating jobs...")
    for node in nodes:
        remote_jobs = ShamiraClient(
            node_host=node.hostname,
            get_session=get_client_session,
            current_node=get_current_node(db),
            keypair_file_path=keypair_file_path,
        ).get_jobs()
        local_jobs_hash_ids = [job.hash_id for job in db.query(Job).all()]
        for remote_job in remote_jobs:
            if remote_job["hash_id"] in local_jobs_hash_ids:
                local_jobs_hash_ids.remove(remote_job["hash_id"])

            existing_job = (
                db.query(Job).filter(Job.hash_id == remote_job["hash_id"]).first()
            )
            if not existing_job:
                new_job = Job(
                    name=remote_job["name"],
                    expiration_time=remote_job.get("expiration_time"),
                    created_time=remote_job.get("created_time"),
                    program_id=remote_job.get("program_id"),
                    status=remote_job["status"],
                    hash_id=remote_job.get("hash_id", ""),
                    signature=remote_job.get("signature", ""),
                )
                db.add(new_job)
                db.commit()
                db.refresh(new_job)
                for step in remote_job["steps"]:
                    db_step = Step(
                        status=step["status"],
                        parameters=step["parameters"],
                        assigned_node_public_key=step["assigned_node_public_key"],
                        dest_node_public_key=step["dest_node_public_key"],
                        step_type=step["step_type"],
                        variable_type=step["variable_type"],
                        variable_name=step["variable_name"],
                        output_value=step["output_value"],
                        hash_id=step.get("hash_id", ""),
                        signature=step.get("signature", ""),
                    )
                    db_step.job_hash_id = remote_job["hash_id"]
                    db.add(db_step)

                db.commit()
                logger.info(
                    f"Propagated job {remote_job['name']} from node {node.hostname}"
                )
            else:

                # if existing_job.status!=JobStatusEnum.started:
                #     continue

                for remote_step in remote_job["steps"]:
                    existing_step = (
                        db.query(Step)
                        .filter(
                            Step.hash_id == remote_step["hash_id"],
                            Step.job_hash_id == existing_job.hash_id,
                        )
                        .first()
                    )

                    if not existing_step:
                        raise Exception("Step not found")

                    if remote_step["status"] != existing_step.status:

                        if existing_step.status == StepStatusEnum.waiting:
                            if StepStatusEnum[remote_step["status"]] in [
                                StepStatusEnum.started,
                                StepStatusEnum.completed,
                                StepStatusEnum.error,
                            ]:
                                existing_step.status = StepStatusEnum[
                                    remote_step["status"]
                                ]
                                existing_step.output_value = remote_step["output_value"]

                        if existing_step.status == StepStatusEnum.started:
                            if StepStatusEnum[remote_step["status"]] in [
                                StepStatusEnum.completed,
                                StepStatusEnum.error,
                            ]:
                                existing_step.status = StepStatusEnum[
                                    remote_step["status"]
                                ]
                                existing_step.output_value = remote_step["output_value"]

                        if existing_step.status == StepStatusEnum.completed:
                            pass

                        db.commit()

        # for job_hash_id in local_jobs_hash_ids:
        #     logger.info(f"Propagating local job with hash_id {job_hash_id} to node {node.hostname}")
        #     job_to_propagate = db.query(Job).filter(Job.hash_id == job_hash_id).first()
        #     ShamiraClient(node_host=node.hostname, get_session=get_client_session, current_node=get_current_node(db), keypair_file_path=keypair_file_path).create_job(JobSchema.model_validate(job_to_propagate))
        #     logger.info(f"Propagated local job {job_to_propagate.name} to node {node.hostname}")


def repeated_task() -> None:
    """This function will run every 1 second in the background."""

    db = (get_db_session()).__next__()
    logger.info("Running main loop tasks...")

    get_client_session = None

    check_node_network(db, get_client_session=get_client_session)

    propagate_nodes(db, get_client_session=get_client_session)

    start_stop_jobs(db, get_client_session=get_client_session)

    run_jobs(db, get_client_session=get_client_session)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()

    db = (get_db_session()).__next__()
    load_initial_nodes(db)

    scheduler.add_job(repeated_task, trigger="cron", second="0-30")
    scheduler.start()

    yield

    scheduler.shutdown()


# Initialize FastAPI app
app = FastAPI(lifespan=lifespan)


class JWTBearer(HTTPBearer):
    def __init__(self, auto_error: bool = True):
        super(JWTBearer, self).__init__(auto_error=auto_error)

    async def __call__(self, request: Request, db: Session = Depends(get_db_session)):
        credentials: HTTPAuthorizationCredentials = await super(
            JWTBearer, self
        ).__call__(request)
        # print(f"Credentials: {credentials}")
        if credentials:
            if not credentials.scheme == "Bearer":
                raise HTTPException(
                    status_code=403, detail="Invalid authentication scheme."
                )
            print(f"Verifying token: {credentials.credentials}")
            if not self.verify_jwt(credentials.credentials, db):
                raise HTTPException(
                    status_code=403, detail="Invalid token or expired token."
                )
            return credentials.credentials
        else:
            raise HTTPException(status_code=403, detail="Invalid authorization code.")

    def verify_jwt(self, token: str, session: Session) -> bool:

        token_str = token
        login_credentials = (
            session.query(Token).filter(Token.token == token_str).first()
        )
        if not login_credentials:
            return False
        expiration_time = login_credentials.expiration_time
        current_time = int(time())
        if current_time > expiration_time:
            return False

        return True


def get_logged_in_node(
    credentials: HTTPAuthorizationCredentials = Depends(JWTBearer),
) -> Node:
    token = credentials.credentials

    session = Depends(get_db_session)
    public_key: str = (
        session.query(Token).filter(Token.token == token).first().public_key
    )
    if public_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    current_node: Node = (
        session.query(Node).filter(Node.public_key == public_key).first()
    )
    if current_node is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return current_node


@app.get("/ping")
def health():
    return {"Hello": "World"}


@app.get("/health")
def health():
    return {"Hello": "World"}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(db: Session = Depends(get_db_session)):
    import html

    nodes = db.query(Node).all()
    jobs = db.query(Job).all()
    steps = db.query(Step).all()

    nodes_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(node.id))}</td>"
        f"<td>{html.escape(node.hostname or '')}</td>"
        f"<td>{html.escape(node.alias or '')}</td>"
        f"<td>{html.escape(node.description or '')}</td>"
        f"<td>{html.escape(str(node.is_current_node))}</td>"
        f"<td>{html.escape(str(node.health_status.value if node.health_status else ''))}</td>"
        f"<td>{html.escape(node.public_key or '')}</td>"
        "</tr>"
        for node in nodes
    )

    jobs_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(job.id))}</td>"
        f"<td>{html.escape(job.hash_id or '')}</td>"
        f"<td>{html.escape(job.name or '')}</td>"
        f"<td>{html.escape(str(job.status.value if hasattr(job.status, 'value') else job.status))}</td>"
        f"<td>{html.escape(str(job.created_time))}</td>"
        f"<td>{html.escape(str(job.expiration_time))}</td>"
        "</tr>"
        for job in jobs
    )

    steps_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(step.hash_id))}</td>"
        f"<td>{html.escape(step.job_hash_id or '')}</td>"
        f"<td>{html.escape(str(step.status.value if hasattr(step.status, 'value') else step.status))}</td>"
        f"<td>{html.escape(str(step.step_type.value if hasattr(step.step_type, 'value') else step.step_type))}</td>"
        f"<td>{html.escape(step.variable_name or '')}</td>"
        f"<td>{html.escape(step.parameters or '')}</td>"
        f"<td>{html.escape(str(step.variable_type.value if hasattr(step.variable_type, 'value') else step.variable_type))}</td>"
        f"<td>{html.escape(step.assigned_node_public_key or '')}</td>"
        f"<td>{html.escape(step.dest_node_public_key or '')}</td>"
        f"<td>{html.escape(step.output_value or '')}</td>"
        "</tr>"
        for step in steps
    )

    html_content = f"""
    <!DOCTYPE html>
    <html lang=\"en\">
    <head>
        <meta charset=\"UTF-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
        <title>Shamira Dashboard</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #f7f7f7; }}
            h1 {{ margin-bottom: 8px; }}
            h2 {{ margin-top: 32px; }}
            table {{ width: 100%; border-collapse: collapse; background: #fff; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 13px; }}
            th {{ background: #f0f0f0; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .note {{ color: #666; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class=\"container\">
            <h1>Shamira Dashboard</h1>
            <div class=\"note\">Overview of Nodes, Jobs, and Steps</div>

            <h2>Nodes</h2>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Hostname</th>
                        <th>Alias</th>
                        <th>Description</th>
                        <th>Current</th>
                        <th>Health</th>
                        <th>Public Key</th>
                    </tr>
                </thead>
                <tbody>
                    {nodes_rows}
                </tbody>
            </table>

            <h2>Jobs</h2>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Hash ID</th>
                        <th>Name</th>
                        <th>Status</th>
                        <th>Created Time</th>
                        <th>Expiration Time</th>
                    </tr>
                </thead>
                <tbody>
                    {jobs_rows}
                </tbody>
            </table>

            <h2>Steps</h2>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Job Hash</th>
                        <th>Status</th>
                        <th>Type</th>
                        <th>Variable</th>
                        <th>Parameters</th>
                        <th>Variable Type</th>
                        <th>Assigned Node</th>
                        <th>Dest Node</th>
                        <th>Output</th>
                    </tr>
                </thead>
                <tbody>
                    {steps_rows}
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@app.get("/nodes", response_model=List[NodeSchema], dependencies=[Depends(JWTBearer())])
def list_nodes(db: Session = Depends(get_db_session)):
    nodes = db.query(Node).all()
    return nodes


@app.post("/node", response_model=NodeSchema, dependencies=[Depends(JWTBearer())])
def create_node(*, session: Session = Depends(get_db_session), node: NodeSchema):

    db_node = Node(**node.model_dump())
    session.add(db_node)
    session.commit()
    session.refresh(db_node)
    return db_node


@app.get("/jobs", response_model=List[JobSchema], dependencies=[Depends(JWTBearer())])
def list_jobs(db: Session = Depends(get_db_session)):
    jobs = db.query(Job).all()
    return jobs


@app.post("/job", response_model=JobSchema, dependencies=[Depends(JWTBearer())])
def create_job(*, session: Session = Depends(get_db_session), job: JobSchema):
    job_data = job.model_dump(exclude={"steps"})
    if not job_data.get("hash_id"):
        job_data["hash_id"] = f"job-{uuid4().hex}"
    job_data[
        "id"
    ] = None  # Ensure ID is None so that it will be auto-generated by the database
    db_job = Job(**job_data)
    session.add(db_job)
    session.commit()
    session.refresh(db_job)

    for step in job.steps:
        step_data = step.model_dump()
        db_step = Step(**step_data)
        db_step.job_hash_id = db_job.hash_id
        if not db_step.hash_id:
            db_step.hash_id = f"step-{uuid4().hex}"
        session.add(db_step)

    session.commit()
    return db_job


@app.post("/auth/login")
async def login(
    *,
    session: Session = Depends(get_db_session),
    login_credentials: LoginCredentialsSchema,
):
    # The 'credentials.credentials' will contain the signed message/signature in a specific format
    # The actual implementation needs to define how client sends key_id, message, and signature
    # This is a simplified outline

    # all_nodes = (session.query(Node).all())
    # for n in all_nodes:
    #     print(n.public_key)

    if (
        not session.query(Node)
        .filter(Node.public_key == login_credentials.public_key)
        .first()
    ):
        return HTTPException(status_code=404, detail="Public key not registered")

    is_valid = verify_signature(
        public_key=Point(None, None).deserialize(login_credentials.public_key),
        message=bytes.fromhex(login_credentials.message),
        signature=(login_credentials.signature["r"], login_credentials.signature["s"]),
    )

    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Generate a token (for simplicity, using a dummy token here)
    token_str = os.urandom(16).hex()
    expiration_time = int(time()) + 60 * 60  # Token valid for 1 hour
    token = Token(
        token=token_str,
        public_key=login_credentials.public_key,
        expiration_time=expiration_time,
    )
    session.add(token)
    session.commit()
    session.refresh(token)
    return {"token": token.token, "expiration_time": token.expiration_time}


@app.post("/auth/register")
async def register(node_info: NodeSchema, *, db: Session = Depends(get_db_session)):

    if node_info.public_key:
        existing_node = (
            db.query(Node).filter(Node.public_key == node_info.public_key).first()
        )
        if existing_node:
            raise HTTPException(
                status_code=400, detail="Node with this public key already registered."
            )

    existing_node = db.query(Node).filter(Node.hostname == node_info.hostname).first()
    if existing_node:
        logger.info(
            f"Node with hostname {node_info.hostname} already exists. Updating info."
        )
        existing_node.alias = node_info.alias
        existing_node.description = node_info.description
        if node_info.public_key and not existing_node.public_key:
            existing_node.public_key = node_info.public_key
        db.commit()
        db.refresh(existing_node)

        return existing_node

    new_node = Node(
        hostname=node_info.hostname,
        alias=node_info.alias,
        description=node_info.description,
        is_current_node=node_info.is_current_node,
        health_status=MynodeHealthStatusEnum.healthy,
        public_key=node_info.public_key,
    )
    db.add(new_node)
    db.commit()
    db.refresh(new_node)

    return new_node


# OLE beaver triple generation protocol
# X = Xa, Xb
# Y = Ya, Yb
# n = na, nb
# Z = (Xa + Xb)*(Ya + Yb) = Xa*Ya + Xa*Yb + Xb*Ya + Xb*Yb
# locally generate na
# receive Xb, public key
# send Xb*Ya + na

# receive Xb*Ya + nb
# compute Zb = Xb*Yb + Xb*Ya + Xa*Yb + nXb + nYb - nA - nB
#


# beaver multiplication
# mark job as started
# send a+x and b+y shares to all nodes
# waiting for a+x and b+y shares from all nodes


# 0 0 0 0 0
# 0 0 0 0 0
# 0 0 1 0 0
# 0 0 0 0 0

# 1 2 3 4 5
