# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json
import os
import subprocess
import urllib3.util
from hdijupyterutils.ipywidgetfactory import IpyWidgetFactory
from sparkmagic.auth.customauth import Authenticator
from sparkmagic.livyclientlib.exceptions import BadUserConfigurationException
from sparkmagic.utils.constants import WIDGET_WIDTH
from google.cloud import dataproc_v1beta2
import google.auth.transport.requests 
from google.auth import _cloud_sdk  
from google.auth.exceptions import UserAccessTokenError
from google.oauth2.credentials import Credentials


# The name of the Cloud SDK shell script
_CLOUD_SDK_POSIX_COMMAND = "gcloud"
_CLOUD_SDK_WINDOWS_COMMAND = "gcloud.cmd"
# The command to get all credentialed accounts 
_CLOUD_SDK_USER_CREDENTIALED_ACCOUNTS_COMMAND = ("auth", "list", "--format", "json")


def list_accounts_pairs(credentialed_accounts, default_credentials_configured):
    """Reformats all of user's credentialed accounts to populate google_credentials_widget
    dropdown's options. 

    Args:
        credentialed_accounts (str): user credentialed account to return credentials for
        default_credentials_configured (boolean): This is True if google application-default
        credentials are configured.

    Returns:
        dict: each key is a str of the users credentialed accounts and it maps to the 
        same str credentialed account
    """
    accounts = credentialed_accounts
    accounts_dict = {}
    for account in accounts:
        accounts_dict[account] = account
    if default_credentials_configured:
        accounts_dict['default-credentials'] = 'default-credentials'
    return accounts_dict

def list_credentialed_accounts():
    """Load all of user's credentialed accounts with ``gcloud auth list`` command.

    Returns:
        Sequence[str]: each key is a str of one of the users credentialed accounts

    Raises:
        sparkmagic.livyclientlib.BadUserConfigurationException: if gcloud cannot be invoked
    """
    accounts_json = ""
    if os.name == "nt":
        command = _CLOUD_SDK_WINDOWS_COMMAND
    else:
        command = _CLOUD_SDK_POSIX_COMMAND
    try:
        command = (command,) + _CLOUD_SDK_USER_CREDENTIALED_ACCOUNTS_COMMAND
        # run `gcloud auth list` command 
        accounts_json = subprocess.check_output(command, stderr=subprocess.STDOUT)
        all_accounts = json.loads(accounts_json)
        credentialed_accounts = set()
        active_account = None
        for account in all_accounts:
            try:
                _cloud_sdk.get_auth_access_token(account['account'])
                if account['status'] == 'ACTIVE':
                    active_account = account['account']
                credentialed_accounts.add(account['account'])
            # when`gcloud auth print-access-token --account=account` fails we don't add it to
            # credentialed_accounts dict that populates account dropdown widget
            except UserAccessTokenError:
                pass
        return credentialed_accounts, active_account
    except (subprocess.CalledProcessError, OSError, IOError) as caught_exc:
        new_exc = BadUserConfigurationException("Gcloud cannot be invoked.")
        raise new_exc from caught_exc

def get_credentials_for_account(account, scopes_list):
    """Load all of user's credentialed accounts with ``gcloud auth describe ACCOUNT`` command.

    Args:
        account (str): user credentialed account to return credentials for
        scopes_list (Sequence[str]): list of scopes to include in the credentials.
    
    Returns:
        google.oauth2.credentials.Credentials: The constructed credentials

    Raises:
        ValueError: If `gcloud auth describe ACCOUNT --format json` returns json not in the expected format.
        google.auth.exceptions.UserAccessTokenError: if credentials could not be found for the given account. 
    """
    if os.name == "nt":
        command = _CLOUD_SDK_WINDOWS_COMMAND
    else:
        command = _CLOUD_SDK_POSIX_COMMAND
    try:
        describe_account_command = ("auth", "describe", account, '--format', 'json')
        command = (command,) + describe_account_command
        account_json = subprocess.check_output(command, stderr=subprocess.STDOUT)
        account_describe = json.loads(account_json)
        return Credentials.from_authorized_user_info(account_describe, scopes=scopes_list)
    # subprocess.CalledProcessError, OSError, IOError are from subprocess
    # ValueError is from Credentials.from_authorized_user_info
    # JSONDecodeError is from json.loads.
    except (subprocess.CalledProcessError, OSError, IOError, ValueError, json.JSONDecodeError) as caught_exc:
        new_exc = UserAccessTokenError(f"Could not obtain access token for {account}")
        raise new_exc from caught_exc

def get_component_gateway_url(project_id, region, cluster_name, credentials):
    """Gets the component gateway url for a cluster name, project id, and region

    Args:
        cluster_name (str): The cluster name to use for the url
        project_id (str): The project id to use for the url
        region (str): The project id to use for the url
        credentials (google.oauth2.credentials.Credentials): The authorization credentials to
        attach to requests.

    Returns:
        str: the component gateway url

    Raises:
        google.api_core.exceptions.GoogleAPICallError: If the request failed for any reason.
        google.api_core.exceptions.RetryError: If the request failed due to a retryable error and retry attempts failed.
        ValueError: If the parameters are invalid.
    """
    client = dataproc_v1beta2.ClusterControllerClient(credentials=credentials,
                       client_options={
                            "api_endpoint": f"{region}-dataproc.googleapis.com:443"
                        }
                    )
    try:
        response = client.get_cluster(project_id, region, cluster_name)
        url = response.config.endpoint_config.http_ports.popitem()[1]
        parsed_uri = urllib3.util.parse_url(url)
        endpoint_address = f"{parsed_uri.scheme}://{parsed_uri.netloc}/" + "gateway/default/livy/v1"
        return endpoint_address
    except:
        raise

def application_default_credentials_configured():
    """Checks if google application-default credentials are configured"""
    try:
        credentials, project = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform', 'https://www.googleapis.com/auth/userinfo.email'])
    except:
        return False
    return credentials is not None


class GoogleAuth(Authenticator):
    """Custom Authenticator to use Google OAuth with SparkMagic."""

    def __init__(self, parsed_attributes=None):
        self.callable_request = google.auth.transport.requests.Request()
        self.credentialed_accounts, active_user_account = list_credentialed_accounts()
        self.scopes = ['https://www.googleapis.com/auth/cloud-platform', 'https://www.googleapis.com/auth/userinfo.email']
        self.active_credentials = None
        self.default_credentials_configured = application_default_credentials_configured()
        account_dict = list_accounts_pairs(self.credentialed_accounts, self.default_credentials_configured)

        if parsed_attributes is not None:
            if parsed_attributes.account in account_dict:
                self.active_credentials = parsed_attributes.account
                if self.active_credentials == 'default-credentials' and self.default_credentials_configured:
                    self.credentials, self.project = google.auth.default(scopes=self.scopes)
                else:
                    self.credentials = get_credentials_for_account(self.active_credentials, self.scopes)
            else: 
                new_exc = BadUserConfigurationException(
                f"{parsed_attributes.account} is not a credentialed account. Run `gcloud auth login` in "\
                "your command line to authorize gcloud to access the Cloud Platform with Google user "\
                "credentials to authenticate. Run `gcloud auth application-default login` acquire new "\
                "user credentials to use for Application Default Credentials. Run `gcloud auth list` to see "\
                "your credentialed accounts.")
                raise new_exc
        else:
            if self.default_credentials_configured:
                self.credentials, self.project = google.auth.default(scopes=self.scopes)
                self.active_credentials = 'default-credentials'
            elif active_user_account is not None:
                self.credentials = get_credentials_for_account(active_user_account, self.scopes)
                self.active_credentials = active_user_account
            else:
                self.credentials, self.project = None, None
        Authenticator.__init__(self, parsed_attributes)
        self.widgets = self.get_widgets(WIDGET_WIDTH)

    def get_widgets(self, widget_width):
        ipywidget_factory = IpyWidgetFactory()

        self.project_widget = ipywidget_factory.get_text(
            description='Project:',
            width=widget_width
        )

        self.cluster_name_widget = ipywidget_factory.get_text(
            description='Cluster:',
            width=widget_width
        )

        self.region_widget = ipywidget_factory.get_text(
            description='Region:',
            width=widget_width
        )

        self.google_credentials_widget = ipywidget_factory.get_dropdown(
            options=list_accounts_pairs(self.credentialed_accounts, self.default_credentials_configured),
            value=None,
            description=u"Account:"
        )

        if self.active_credentials is not None: 
            self.google_credentials_widget.value = self.active_credentials
        else: 
            self.google_credentials_widget.disabled = True

        widgets = [self.project_widget, self.region_widget, self.cluster_name_widget, self.google_credentials_widget]
        return widgets

    def initialize_credentials_with_auth_account_selection(self, account):
        """Initializes self.credentials with the accound selected from the auth dropdown widget"""
        if account != self.active_credentials:
            if account == 'default-credentials':
                self.credentials, self.project = google.auth.default(scopes=self.scopes)
            else:
                self.credentials = get_credentials_for_account(account, self.scopes)

    def update_with_widget_values(self):
        no_credentials_exception = BadUserConfigurationException(
            "Failed to obtain access token. Run `gcloud auth login` in your command line "\
            "to authorize gcloud to access the Cloud Platform with Google user credentials to "\
            "authenticate. Run `gcloud auth application-default login` acquire new user "\
            "credentials to use for Application Default Credentials.")
        if self.credentials is not None:
            try:
                self.url = get_component_gateway_url(self.project_widget.value, self.region_widget.value, \
                    self.cluster_name_widget.value, self.credentials)
            except:
                raise
        else:
            raise no_credentials_exception
        self.initialize_credentials_with_auth_account_selection(self.google_credentials_widget.value)

    def __call__(self, request):
        if not self.credentials.valid:
            self.credentials.refresh(self.callable_request)
        request.headers['Authorization'] = f'Bearer {self.credentials.token}'
        return request

    def __hash__(self):
        return hash((self.active_credentials, self.url, self.__class__.__name__))
