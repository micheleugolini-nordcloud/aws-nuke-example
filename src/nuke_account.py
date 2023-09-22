from __future__ import annotations

import logging
import os
import stat
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from tempfile import NamedTemporaryFile, TemporaryDirectory, TemporaryFile
from typing import Any, Dict, Optional, Union

from boto3 import Session
from botocore.client import BaseClient
from botocore.config import Config

LOG = logging.getLogger()


@dataclass(frozen=True, eq=False)
class AwsCredentials:
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: Optional[str] = field(default=None)
    expiration: Optional[datetime] = field(default=None)

    @classmethod
    def from_assume_role_response(
        cls,
        response: Dict[str, Union[str, datetime]],
    ) -> AwsCredentials:
        """
        Create an instance of the class based on the response of the STS boto3 client
        """
        credentials: Dict[str, Union[str, datetime]] = response["Credentials"]
        # otherwise we will put in the logs all the credentials for each account
        LOG.debug("AwsCredentials Keys from assume role: %s", response.keys())
        return cls(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            expiration=credentials["Expiration"],
        )

    def to_boto_session_args(self, region_name: str = None) -> Dict[str, str]:
        """
        Return the dictionary to be used with boto.Session constructor
        """
        credentials = asdict(self)
        credentials.pop("expiration", None)
        if region_name:
            credentials["region_name"] = region_name
        LOG.debug("AwsCredentials Keys to boto session args: %s", credentials.keys())
        return credentials


def get_temporary_aws_credentials(
    session: Session,
    role_arn: str,
    sts_client: BaseClient = None,
    region_name: str = None,
    botocore_config: Config = None,
    session_name: str = "Nuke",
    session_duration_seconds: int = 1800,
    external_id: str = None,
) -> AwsCredentials:
    """
    Assume a role and return the temporary credentials for this role

    Returns:
        (AwsCredentials): Returns access key, secret access key and session token
    """
    sts = sts_client or session.client(
        service_name="sts",
        region_name=region_name,
        config=botocore_config,
    )
    args = {
        "RoleArn": role_arn,
        "RoleSessionName": session_name,
        "DurationSeconds": session_duration_seconds,
    }

    # ExternalId is only an optional argument
    if external_id:
        args["ExternalId"] = external_id

    return AwsCredentials.from_assume_role_response(sts.assume_role(**args))


class NukeAccountHandler:
    def __init__(self):
        self._config_file = "/opt/aws_nuke_config.yml"
        self._zip_package = "/opt/aws_nuke.zip"
        self._dry_run = True

    def __call__(self, event: Dict[str, Any], context: object) -> Dict[str, Any]:
        """
        Entry point of the order processing

        Reference:
            https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html
            https://docs.aws.amazon.com/lambda/latest/dg/python-context.html

        Args:
            event (Dict[str, Any]): the dictionary provided to the Lambda
                function as payload input
            context (object): Object that provides methods, properties and
                information about the invocation, function, and execution
                environment.

        Example:

        >>> # create the Handler
        >>> class NewHandler(BaseLambdaHandler):
        >>>     def __init__(...):
        >>>         ...
        >>>
        >>> if os.environ.get('AWS_EXECUTION_ENV'):
        >>>     lambda_handler = NewHandler()
        """
        return self._nuke_account(event=event)

    def _format_nuke_conf_file(self, account_id: str) -> TemporaryFile:
        """
         Preprocess file aws_nuke_config to insert the account id to be nuked
        Args:
            account_id: account id to be nuked

        Returns: nuke config file with targeted account id

        """
        lines = []
        with open(self._config_file) as f:
            lines = f.readlines()

        tmp_file = NamedTemporaryFile(suffix=".yml")
        with open(tmp_file.name, "w") as outfile:
            for line in lines:
                outfile.write(line.replace("{account_id}", account_id))

        return tmp_file

    def _nuke_account(self, event: Dict[str, Any]) -> Dict[str, Any]:
        account_id = event["account_id"]
        LOG.debug("Delete all resources in AWS account %s.", account_id)

        nuke_config_file = self._format_nuke_conf_file(account_id=account_id)

        region_name = "region"

        tmp_dir = TemporaryDirectory()
        with zipfile.ZipFile(self._zip_package, "r") as zip_ref:
            zip_ref.extractall(tmp_dir.name)
        os.chmod(
            "{tmp_dir_name}/aws-nuke".format(tmp_dir_name=tmp_dir.name), stat.S_IRWXU
        )
        # you may no need this if the role of the lambda is already the role
        # you want to use for the nuke
        credentials = get_temporary_aws_credentials(
            session=Session(),
            role_arn="arn{}role".format(account_id),
            region_name=region_name,
            session_duration_seconds=900,
        )
        aws_nuke_result_message = (
            f"Successfully deleted all resources for account {account_id}"
        )
        aws_nuke_result = None
        try:
            cmd_args = [
                "{tmp_dir_name}/aws-nuke".format(tmp_dir_name=tmp_dir.name),
                "-c",
                nuke_config_file.name,
                "--access-key-id",
                credentials.aws_access_key_id,
                "--secret-access-key",
                credentials.aws_secret_access_key,
                "--session-token",
                credentials.aws_session_token,
            ]
            # to preserve order of cmd args
            if self._dry_run:
                cmd_args.append("--no-dry-run")
            cmd_args.extend(["--force", "--force-sleep", "3"])
            aws_nuke_result = subprocess.check_output(cmd_args).decode(
                sys.stdout.encoding
            )
        except subprocess.CalledProcessError as e:
            if e.returncode != 255:
                raise Exception(
                    f"Error: An error occurred while deleting all resources in account {account_id}. "
                    f"The process returned a non-zero exit code ({e.returncode})."
                )
            else:
                aws_nuke_result_message += f" - with error {e.returncode}"

        return {"aws_nuke_result": " ".join(aws_nuke_result.splitlines()[-3:])}


def create_api_nuke_account_handler():
    return NukeAccountHandler()


if os.environ.get("AWS_EXECUTION_ENV"):
    lambda_handler = create_api_nuke_account_handler()
