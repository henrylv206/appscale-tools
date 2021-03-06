#!/usr/bin/env python
# Programmer: Chris Bunch (chris@appscale.com)


# General-purpose Python library imports
import getpass
import json
import os
import re
import shutil
import sys
import time
import uuid


# AppScale-specific imports
from agents.factory import InfrastructureAgentFactory
from appcontroller_client import AppControllerClient
from appengine_helper import AppEngineHelper
from appscale_logger import AppScaleLogger
from custom_exceptions import AppScaleException
from custom_exceptions import BadConfigurationException
from local_state import APPSCALE_VERSION
from local_state import LocalState
from node_layout import NodeLayout
from remote_helper import RemoteHelper
from user_app_client import UserAppClient


class AppScaleTools():
  """AppScaleTools provides callers with a way to start, stop, and interact
  with AppScale deployments, on virtualized clusters or on cloud
  infrastructures.

  These methods provide an interface for users who wish to start and control
  AppScale through a dict of parameters. An alternative to this method is to
  use the AppScale class, which stores state in an AppScalefile in the
  current working directory (as opposed to a dict), but under the hood these
  methods get called anyways.
  """


  # The number of seconds to wait to give services time to start up or shut
  # down.
  SLEEP_TIME = 5


  # The location of the expect script, used to interact with ssh-copy-id
  EXPECT_SCRIPT = os.path.dirname(__file__) + os.sep + ".." + os.sep + \
    "templates" + os.sep + "sshcopyid"


  # A regular expression that matches compressed App Engine apps.
  TAR_GZ_REGEX = re.compile('.tar.gz\Z')


  @classmethod
  def add_instances(cls, options):
    """Adds additional machines to an AppScale deployment.

    Args:
      options: A Namespace that has fields for each parameter that can be
        passed in via the command-line interface.
    """
    if 'master' in options.ips.keys():
      raise BadConfigurationException("Cannot add master nodes to an " + \
        "already running AppScale deployment.")

    # Skip checking for -n (replication) because we don't allow the user
    # to specify it here (only allowed in run-instances).
    additional_nodes_layout = NodeLayout(options)

    # In virtualized cluster deployments, we need to make sure that the user
    # has already set up SSH keys.
    if LocalState.get_from_yaml(options.keyname, 'infrastructure') == "xen":
      for ip in options.ips.values():
        # throws a ShellException if the SSH key doesn't work
        RemoteHelper.ssh(ip, options.keyname, "ls", options.verbose)

    # Finally, find an AppController and send it a message to add
    # the given nodes with the new roles.
    AppScaleLogger.log("Sending request to add instances")
    login_ip = LocalState.get_login_host(options.keyname)
    acc = AppControllerClient(login_ip, LocalState.get_secret_key(
      options.keyname))
    acc.start_roles_on_nodes(json.dumps(options.ips))

    # TODO(cgb): Should we wait for the new instances to come up and get
    # initialized?
    AppScaleLogger.success("Successfully sent request to add instances " + \
      "to this AppScale deployment.")


  @classmethod
  def add_keypair(cls, options):
    """Sets up passwordless SSH login to the machines used in a virtualized
    cluster deployment.

    Args:
      options: A Namespace that has fields for each parameter that can be
        passed in via the command-line interface.
    """
    LocalState.require_ssh_commands(options.auto, options.verbose)
    LocalState.make_appscale_directory()

    path = LocalState.LOCAL_APPSCALE_PATH + options.keyname
    if options.add_to_existing:
      public_key = path + ".pub"
      private_key = path
    else:
      public_key, private_key = LocalState.generate_rsa_key(options.keyname,
        options.verbose)

    if options.auto:
      if 'root_password' in options:
        AppScaleLogger.log("Using the provided root password to log into " + \
          "your VMs.")
        password = options.root_password
      else:
        AppScaleLogger.log("Please enter the password for the root user on" + \
          " your VMs:")
        password = getpass.getpass()

    node_layout = NodeLayout(options)
    if not node_layout.is_valid():
      raise BadConfigurationException("There were problems with your " + \
        "placement strategy: " + str(node_layout.errors()))

    all_ips = [node.public_ip for node in node_layout.nodes]
    for ip in all_ips:
      # first, set up passwordless ssh
      AppScaleLogger.log("Executing ssh-copy-id for host: {0}".format(ip))
      if options.auto:
        LocalState.shell("{0} root@{1} {2} {3}".format(cls.EXPECT_SCRIPT, ip,
          private_key, password), options.verbose)
      else:
        LocalState.shell("ssh-copy-id -i {0} root@{1}".format(private_key, ip),
          options.verbose)

      # next, copy over the ssh keypair we generate
      RemoteHelper.scp(ip, options.keyname, public_key, '/root/.ssh/id_rsa.pub',
        options.verbose)
      RemoteHelper.scp(ip, options.keyname, private_key, '/root/.ssh/id_rsa',
        options.verbose)

    AppScaleLogger.success("Generated a new SSH key for this deployment " + \
      "at {0}".format(private_key))


  @classmethod
  def describe_instances(cls, options):
    """Queries each node in the currently running AppScale deployment and
    reports on their status.

    Args:
      options: A Namespace that has fields for each parameter that can be
        passed in via the command-line interface.
    """
    login_host = LocalState.get_login_host(options.keyname)
    login_acc = AppControllerClient(login_host,
      LocalState.get_secret_key(options.keyname))

    for ip in login_acc.get_all_public_ips():
      acc = AppControllerClient(ip, LocalState.get_secret_key(options.keyname))
      AppScaleLogger.log("Status of node at {0}:".format(ip))
      try:
        AppScaleLogger.log(acc.get_status())
      except Exception as e:
        AppScaleLogger.warn("Unable to contact machine: {0}\n".format(str(e)))

    AppScaleLogger.success("View status information about your AppScale " + \
      "deployment at http://{0}/status".format(login_host))


  @classmethod
  def gather_logs(cls, options):
    """Collects logs from each machine in the currently running AppScale
    deployment.

    Args:
      options: A Namespace that has fields for each parameter that can be
        passed in via the command-line interface.
    """
    # First, make sure that the place we want to store logs doesn't
    # already exist.
    if os.path.exists(options.location):
      raise AppScaleException("Can't gather logs, as the location you " + \
        "specified, {0}, already exists.".format(options.location))

    acc = AppControllerClient(LocalState.get_login_host(options.keyname),
      LocalState.get_secret_key(options.keyname))

    # do the mkdir after we get the secret key, so that a bad keyname will
    # cause the tool to crash and not create this directory
    os.mkdir(options.location)

    for ip in acc.get_all_public_ips():
      # Get the logs from each node, and store them in our local directory
      local_dir = "{0}/{1}".format(options.location, ip)
      os.mkdir(local_dir)
      RemoteHelper.scp_remote_to_local(ip, options.keyname, '/var/log/appscale',
        local_dir, options.verbose)
    AppScaleLogger.success("Successfully copied logs to {0}".format(
      options.location))


  @classmethod
  def remove_app(cls, options):
    """Instructs AppScale to no longer host the named application.

    Args:
      options: A Namespace that has fields for each parameter that can be
        passed in via the command-line interface.
    """
    if not options.confirm:
      response = raw_input("Are you sure you want to remove this " + \
        "application? (Y/N) ")
      if response not in ['y', 'yes', 'Y', 'YES']:
        raise AppScaleException("Cancelled application removal.")

    login_host = LocalState.get_login_host(options.keyname)
    acc = AppControllerClient(login_host, LocalState.get_secret_key(
      options.keyname))
    userappserver_host = acc.get_uaserver_host(options.verbose)
    userappclient = UserAppClient(userappserver_host, LocalState.get_secret_key(
      options.keyname))
    if not userappclient.does_app_exist(options.appname):
      raise AppScaleException("The given application is not currently running.")

    acc.stop_app(options.appname)
    AppScaleLogger.log("Please wait for your app to shut down.")
    while True:
      if acc.is_app_running(options.appname):
        time.sleep(cls.SLEEP_TIME)
      else:
        break
    AppScaleLogger.success("Done shutting down {0}".format(options.appname))


  @classmethod
  def reset_password(cls, options):
    """Resets a user's password the currently running AppScale deployment.

    Args:
      options: A Namespace that has fields for each parameter that can be
        passed in via the command-line interface.
    """
    secret = LocalState.get_secret_key(options.keyname)
    username, password = LocalState.get_credentials(is_admin=False)
    encrypted_password = LocalState.encrypt_password(username, password)

    acc = AppControllerClient(LocalState.get_login_host(options.keyname),
      secret)
    userappserver_ip = acc.get_uaserver_host(options.verbose)
    uac = UserAppClient(userappserver_ip, secret)

    try:
      uac.change_password(username, encrypted_password)
      AppScaleLogger.success("The password was successfully changed for the " + \
        "given user.")
    except Exception as e:
      AppScaleLogger.warn("Could not change the user's password for the " + \
        "following reason: {0}".format(str(e)))
      sys.exit(1)


  @classmethod
  def run_instances(cls, options):
    """Starts a new AppScale deployment with the parameters given.

    Args:
      options: A Namespace that has fields for each parameter that can be
        passed in via the command-line interface.
    Raises:
      BadConfigurationException: If the user passes in options that are not
        sufficient to start an AppScale deplyoment (e.g., running on EC2 but
        not specifying the AMI to use), or if the user provides us
        contradictory options (e.g., running on EC2 but not specifying EC2
        credentials).
    """
    LocalState.make_appscale_directory()
    LocalState.ensure_appscale_isnt_running(options.keyname, options.force)

    if options.infrastructure:
      AppScaleLogger.log("Starting AppScale " + APPSCALE_VERSION +
        " over the " + options.infrastructure + " cloud.")
    else:
      AppScaleLogger.log("Starting AppScale " + APPSCALE_VERSION +
        " over a virtualized cluster.")
    my_id = str(uuid.uuid4())
    AppScaleLogger.remote_log_tools_state(options, my_id, "started",
      APPSCALE_VERSION)

    node_layout = NodeLayout(options)
    if not node_layout.is_valid():
      raise BadConfigurationException("There were errors with your " + \
        "placement strategy:\n{0}".format(str(node_layout.errors())))

    if not node_layout.is_supported():
      AppScaleLogger.warn("Warning: This deployment strategy is not " + \
        "officially supported.")

    public_ip, instance_id = RemoteHelper.start_head_node(options, my_id,
      node_layout)
    AppScaleLogger.log("\nPlease wait for AppScale to prepare your machines " +
      "for use.")

    # Write our metadata as soon as possible to let users SSH into those
    # machines via 'appscale ssh'
    LocalState.update_local_metadata(options, node_layout, public_ip,
      instance_id)
    RemoteHelper.copy_local_metadata(public_ip, options.keyname,
      options.verbose)

    acc = AppControllerClient(public_ip, LocalState.get_secret_key(
      options.keyname))
    uaserver_host = acc.get_uaserver_host(options.verbose)

    RemoteHelper.sleep_until_port_is_open(uaserver_host, UserAppClient.PORT,
      options.verbose)

    # Update our metadata again so that users can SSH into other boxes that
    # may have been started.
    LocalState.update_local_metadata(options, node_layout, public_ip,
      instance_id)
    RemoteHelper.copy_local_metadata(public_ip, options.keyname,
      options.verbose)

    AppScaleLogger.log("UserAppServer is at {0}".format(uaserver_host))

    uaserver_client = UserAppClient(uaserver_host,
      LocalState.get_secret_key(options.keyname))

    if options.admin_user and options.admin_pass:
      AppScaleLogger.log("Using the provided admin username/password")
      username, password = options.admin_user, options.admin_pass
    elif options.test:
      AppScaleLogger.log("Using default admin username/password")
      username, password = LocalState.DEFAULT_USER, LocalState.DEFAULT_PASSWORD
    else:
      username, password = LocalState.get_credentials()

    RemoteHelper.create_user_accounts(username, password, uaserver_host,
      options.keyname)
    uaserver_client.set_admin_role(username)

    RemoteHelper.wait_for_machines_to_finish_loading(public_ip, options.keyname)
    # Finally, update our metadata once we know that all of the machines are
    # up and have started all their API services.
    LocalState.update_local_metadata(options, node_layout, public_ip,
      instance_id)
    RemoteHelper.copy_local_metadata(public_ip, options.keyname, options.verbose)

    RemoteHelper.sleep_until_port_is_open(LocalState.get_login_host(
      options.keyname), RemoteHelper.APP_DASHBOARD_PORT, options.verbose)
    AppScaleLogger.success("AppScale successfully started!")
    AppScaleLogger.success("View status information about your AppScale " + \
      "deployment at http://{0}/status".format(LocalState.get_login_host(
      options.keyname)))
    AppScaleLogger.remote_log_tools_state(options, my_id,
      "finished", APPSCALE_VERSION)


  @classmethod
  def terminate_instances(cls, options):
    """Stops all services running in an AppScale deployment, and in cloud
    deployments, also powers off the instances previously spawned.

    Raises:
      AppScaleException: If AppScale is not running, and thus can't be
      terminated.
    """
    if not os.path.exists(LocalState.get_locations_yaml_location(
      options.keyname)):
      raise AppScaleException("AppScale is not running with the keyname {0}".
        format(options.keyname))

    if LocalState.get_infrastructure(options.keyname) in \
      InfrastructureAgentFactory.VALID_AGENTS:
      RemoteHelper.terminate_cloud_infrastructure(options.keyname,
        options.verbose)
    else:
      RemoteHelper.terminate_virtualized_cluster(options.keyname,
        options.verbose)

    LocalState.cleanup_appscale_files(options.keyname)
    AppScaleLogger.success("Successfully shut down your AppScale deployment.")


  @classmethod
  def upload_app(cls, options):
    """Uploads the given App Engine application into AppScale.

    Args:
      options: A Namespace that has fields for each parameter that can be
        passed in via the command-line interface.
    Returns:
      A tuple containing the host and port where the application is serving
        traffic from.
    """
    if cls.TAR_GZ_REGEX.search(options.file):
      file_location = LocalState.extract_app_to_dir(options.file,
        options.verbose)
      created_dir = True
    else:
      file_location = options.file
      created_dir = False

    app_id = AppEngineHelper.get_app_id_from_app_config(file_location)
    app_language = AppEngineHelper.get_app_runtime_from_app_config(
      file_location)
    AppEngineHelper.validate_app_id(app_id)

    acc = AppControllerClient(LocalState.get_login_host(options.keyname),
      LocalState.get_secret_key(options.keyname))
    userappserver_host = acc.get_uaserver_host(options.verbose)
    userappclient = UserAppClient(userappserver_host, LocalState.get_secret_key(
      options.keyname))

    if options.test:
      username = LocalState.DEFAULT_USER
    elif options.email:
      username = options.email
    else:
      username = LocalState.get_username_from_stdin(is_admin=False)

    if not userappclient.does_user_exist(username):
      password = LocalState.get_password_from_stdin()
      RemoteHelper.create_user_accounts(username, password, userappserver_host,
        options.keyname)

    app_exists = userappclient.does_app_exist(app_id)
    app_admin = userappclient.get_app_admin(app_id)
    if app_admin is not None and username != app_admin:
      raise AppScaleException("The given user doesn't own this application" + \
        ", so they can't upload an app with that application ID. Please " + \
        "change the application ID and try again.")

    if app_exists:
      AppScaleLogger.log("Uploading new version of app {0}".format(app_id))
    else:
      AppScaleLogger.log("Uploading initial version of app {0}".format(app_id))
      userappclient.reserve_app_id(username, app_id, app_language)

    remote_file_path = RemoteHelper.copy_app_to_host(file_location,
      options.keyname, options.verbose)

    acc.done_uploading(app_id, remote_file_path)
    acc.update([app_id])

    # now that we've told the AppController to start our app, find out what port
    # the app is running on and wait for it to start serving
    AppScaleLogger.log("Please wait for your app to start serving.")

    if app_exists:
      time.sleep(20)  # give the AppController time to restart the app

    serving_host, serving_port = userappclient.get_serving_info(app_id,
      options.keyname)
    RemoteHelper.sleep_until_port_is_open(serving_host, serving_port,
      options.verbose)
    AppScaleLogger.success("Your app can be reached at the following URL: " +
      "http://{0}:{1}".format(serving_host, serving_port))

    if created_dir:
      shutil.rmtree(file_location)

    return (serving_host, serving_port)
