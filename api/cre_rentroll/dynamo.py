from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

TABLE = os.environ.get("DDB_TABLE", "ml_interview")


def _resource():
    kwargs: dict[str, Any] = {"region_name": os.environ.get("AWS_REGION", "us-east-1")}
    endpoint = os.environ.get("DDB_ENDPOINT") or ""
    if endpoint:
        kwargs["endpoint_url"] = endpoint
        kwargs["aws_access_key_id"] = os.environ.get("AWS_ACCESS_KEY_ID", "local")
        kwargs["aws_secret_access_key"] = os.environ.get("AWS_SECRET_ACCESS_KEY", "local")
    return boto3.resource("dynamodb", **kwargs)


def table():
    return _resource().Table(TABLE)


def _jsonable(val: Any) -> Any:
    if isinstance(val, Decimal):
        return int(val) if val % 1 == 0 else float(val)
    if isinstance(val, dict):
        return {k: _jsonable(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_jsonable(v) for v in val]
    return val


def ensure_table() -> None:
    import time

    client = _resource().meta.client
    last_err: Exception | None = None
    for _ in range(12):
        try:
            client.describe_table(TableName=TABLE)
            return
        except ClientError:
            break
        except Exception as exc:  # endpoint not ready yet
            last_err = exc
            time.sleep(1)
    else:
        if last_err:
            raise last_err
    client.create_table(
        TableName=TABLE,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=TABLE)


def pk_user(username: str) -> str:
    return f"USER#{username.strip().lower()}"


def login(username: str) -> dict[str, Any]:
    username = username.strip()
    if not username or len(username) > 64 or not username.replace("_", "").replace("-", "").isalnum():
        raise ValueError("username must be 1–64 letters, digits, _ or -")
    t = table()
    key = {"pk": pk_user(username), "sk": "PROFILE"}
    existing = t.get_item(Key=key).get("Item")
    created = False
    if not existing:
        now = datetime.now(timezone.utc).isoformat()
        t.put_item(Item={**key, "username": username, "created_at": now})
        created = True
    return {"username": username, "created": created}


def get_item(username: str, sk: str) -> dict[str, Any] | None:
    item = table().get_item(Key={"pk": pk_user(username), "sk": sk}).get("Item")
    return _jsonable(item) if item else None


def _to_ddb(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, dict):
        return {k: _to_ddb(v) for k, v in val.items() if v is not None}
    if isinstance(val, list):
        return [_to_ddb(v) for v in val]
    return val


def put_item(username: str, sk: str, attrs: dict[str, Any]) -> None:
    item = _to_ddb({"pk": pk_user(username), "sk": sk, **attrs})
    table().put_item(Item=item)


def query_prefix(username: str, sk_prefix: str) -> list[dict[str, Any]]:
    resp = table().query(
        KeyConditionExpression=Key("pk").eq(pk_user(username)) & Key("sk").begins_with(sk_prefix)
    )
    return [_jsonable(i) for i in resp.get("Items", [])]
