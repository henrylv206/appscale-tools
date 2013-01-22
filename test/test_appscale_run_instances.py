#!/usr/bin/env python
# Programmer: Chris Bunch (chris@appscale.com)


# General-purpose Python library imports
import os
import sys
import unittest


# AppScale import, the library that we're testing here
lib = os.path.dirname(__file__) + os.sep + ".." + os.sep + "lib"
sys.path.append(lib)
from appscale_tools import AppScaleTools
from custom_exceptions import BadConfigurationException
from parse_args import ParseArgs

import common_functions
import vm_tools


class TestAppScaleRunInstances(unittest.TestCase):


  def setUp(self):
    self.argv = ["--min", "1", "--max", "1"]

    for var in vm_tools.EC2_ENVIRONMENT_VARIABLES:
      os.environ[var] = "BOO"


    """
    file = flexmock(File)
    file.should_receive(:exists?).and_return(true)
"""

  def test_machine_not_set_in_cloud_deployments(self):
    argv = self.argv[:] + ["--infrastructure", "euca"]
    options = ParseArgs(argv, "appscale-run-instances").args

    tools = AppScaleTools()
    self.assertRaises(BadConfigurationException,
      tools.run_instances, options)

"""
  def test_environment_variables_not_set_in_cloud_deployments
    options = {
      "infrastructure" => "euca",
      "machine" => "emi-ABCDEFG"
    }

    EC2_ENVIRONMENT_VARIABLES.each { |var|
      ENV[var] = nil
    }

    assert_raises(BadConfigurationException) {
      AppScaleTools.run_instances(options)
    }
  end

  def test_usage_is_up_to_date
    AppScaleTools::RUN_INSTANCES_FLAGS.each { |flag|
      assert_equal(true, 
        AppScaleTools::RUN_INSTANCES_USAGE.include?("-#{flag}"), 
        "No usage text for #{flag}.")
    } 
  end
end
"""
