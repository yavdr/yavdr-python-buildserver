#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from socketserver import ThreadingMixIn
import argparse
import ast
import configparser
import hashlib
import hmac
import datetime
import json
import pprint
import os
import shutil
import signal
import subprocess
import sys
import tempfile
#import threading

config = None
server = None


class Config:
    def __init__(self):
        argparser = argparse.ArgumentParser(description='Github hook handler')
        argparser.add_argument('-c', '--config', action='append', metavar='CONFIG', dest='config', default=None, help='configuration file(s)')
        args = vars(argparser.parse_args())
        self.configparser = configparser.SafeConfigParser()
        self.configparser.read(args["config"])
        self.get_config()
        # set up environment variables
        try:
            self.HOOK_SECRET_KEY = os.environb[b'HOOK_SECRET_KEY']
        except:
            print("warning: HOOK_SECRET_KEY environment variable not set!")
            print("export your buildhook secret as HOOK_SECRET_KEY")
            self.HOOK_SECRET_KEY = None
        os.environ['DEBEMAIL'] = self.debemail
        os.environ['DEBFULLNAME'] = self.debfullname
        os.environ['EDITOR'] = 'true'

    def get_setting(self, category, setting, default = None):
        if self.configparser.has_option(category, setting):
            return self.configparser.get(category, setting)
        else:
            return default

    def get_settingb(self, category, setting, default = False):
        if self.configparser.has_option(category, setting):
            return self.configparser.getboolean(category, setting)
        else:
            return default

    def get_section(self, section, default = None):
        if self.configparser.has_section(section):
            return self.configparser[section]
        else:
            return default

    def get_config(self):
        self.dryrun = self.get_settingb("Server", "dryrun", False)
        self.server_port = int(self.get_setting("Server", "port", "8180"))
        
        self.launchpad_owner = self.get_setting("Launchpad", "owner", "yavdr")
        
        self.github_owner = self.get_setting("Github", "owner", "yavdr")
        self.github_baseurl = self.get_setting("Github", "baseurl", "git://github.com/yavdr/")
        
        self.debfullname = self.get_setting("Build", "fullname", "yaVDR Release-Team")
        self.debemail = self.get_setting("Build", "email", "release@yavdr.org")
        self.gpgkey = self.get_setting("Build", "gpgkey", None)
        self.version_suffix = self.get_setting("Build", "version_suffix", "-0yavdr0~{release}")
        self.default_release = self.get_setting("Build", "default_release", "trusty")
        self.default_stage = self.get_setting("Build", "default_stage", "unstable")
        self.default_section = self.get_setting("Build", "default_section", "main")

        self.stages = self.get_section("Stages", {'master': 'unstable', 'testing-': 'testing', 'stable-': 'stable'})
        self.releases = self.get_section("Releases", {'-0.5': 'precise', '-0.6': 'trusty'})
        self.sections = self.get_section("Sections", {'vdr-': 'vdr', 'vdr-addon-': 'main', 'yavdr-': 'yavdr'})


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""


class GithubHookHandler(BaseHTTPRequestHandler):
    """Base class for webhook handlers.

    Subclass it and implement 'handle_payload'.
    """
    def _validate_signature(self, data):
        if config.HOOK_SECRET_KEY:
            sha_name, signature = self.headers['X-Hub-Signature'].split('=')
            if sha_name != 'sha1':
                return False

            # HMAC requires its key to be bytes, but data is strings.
            mac = hmac.new(config.HOOK_SECRET_KEY, msg=data, digestmod=hashlib.sha1)
            return hmac.compare_digest(mac.hexdigest(), signature)
        else:
            return True

    def do_POST(self):
        data_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(data_length)

        if not self._validate_signature(post_data):
            self.send_response(401)
            return

        # first send response
        self.send_response(200)
        self.end_headers()
        self.flush_headers()
        # then handle request, so that no timeout occurs
        payload = json.loads(post_data.decode('utf-8'))
        self.handle_payload(payload)


class Build:
    def __init__(self):
        self.pusher = ""
        self.pusher_email = ""
        self.owner = ""
        self.name = ""
        self.git_url = ""
        self.branch = ""
        self.stage = ""
        self.release = ""
        self.section = ""
        self.urgency = ""


class MyHandler(GithubHookHandler):
    def handle_payload(self, json_payload):
        build = Build()
        build.pusher = json_payload["pusher"]["name"]
        build.pusher_email = json_payload["pusher"]["email"]
        build.owner = json_payload["repository"]["owner"]["name"]
        build.name = json_payload["repository"]["name"]
        build.git_url = json_payload["repository"]["git_url"]
        branch = json_payload["ref"]

        if build.owner != config.github_owner:
            raise Exception("wrong owner")
        if not build.git_url.startswith(config.github_baseurl):
            raise Exception("wrong repository")
        if not branch.startswith("refs/heads/"):
            raise Exception("unknown branch")

        build.branch = branch[11:]

        build.stage = config.default_stage
        matches = [sta for sta in config.stages.keys() if build.branch.startswith(sta)]
        if len(matches) > 0:
            max_length, longest_element = max([(len(x),x) for x in matches])
            build.stage = config.stages[longest_element]

        build.release = config.default_release
        matches = [rel for rel in config.releases.keys() if build.branch.endswith(rel)]
        if len(matches) > 0:
            max_length, longest_element = max([(len(x),x) for x in matches])
            build.release = config.releases[longest_element]

        matches = [sec for sec in config.sections.keys() if build.name.startswith(sec)]
        if len(matches) == 0:
            raise Exception("unknown section")
        max_length, longest_element = max([(len(x),x) for x in matches])
        build.section = config.sections[longest_element]

        build.urgency = "medium"

        print("repo:    ", build.name)
        print("branch:  ", build.branch)
        print("owner:   ", build.owner)
        print("pusher:  ", build.pusher)
        print("pusher-m:", build.pusher_email)
        print("git_url: ", build.git_url)
        print("stage:   ", build.stage)
        print("section: ", build.section)
        print("release: ", build.release)
        print("urgency: ", build.urgency)

        version_suffix = config.version_suffix.replace("{release}", build.release)
        date = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        print("date:    ", date)
        if build.section == "main" and build.section != "unstable":
            lprepo = "main"
        else:
            lprepo = "{STAGE}-{SECTION}".format(STAGE=build.stage, SECTION=build.section)
        print("lprepo:  ", lprepo)

        package_version = "{DATE}{STAGE}".format(DATE=date, STAGE=build.stage)
        package_name_version = "{PACKAGE_NAME}_{PACKAGE_VERSION}".format(
            PACKAGE_NAME=build.name, PACKAGE_VERSION=package_version)
        orig_file = "{PACKAGE_NAME_VERSION}.orig.tar.gz".format(
            PACKAGE_NAME_VERSION=package_name_version)
        changes_file = "{PACKAGE_NAME_VERSION}{VERSION_SUFFIX}_source.changes".format(
            PACKAGE_NAME_VERSION=package_name_version,
            VERSION_SUFFIX=version_suffix)
        ppa = "ppa:{PPA_OWNER}/{LPREPO}".format(
            PPA_OWNER=config.launchpad_owner, LPREPO=lprepo)
        print("ppa:     ", ppa)
        print("version_suffix:", version_suffix)

        try:
            # create a temporary directory and enter it
            tmpdir = tempfile.mkdtemp(suffix=build.name)
            os.chdir(tmpdir)

            # log the output to files
            logfile = open('build.log', 'w+b')
            errorfile = open('error.log', 'w+b')

            print("checkout sourcecode")
            subprocess.check_call(["git", "clone", "-b", build.branch, build.git_url,
                                   package_name_version],
                                  stdout=logfile, stderr=errorfile)
            os.chdir(os.path.join(tmpdir, package_name_version))
            print("get commit_id")
            commit_id = subprocess.check_output(["git", "rev-parse", "HEAD"])
            print("rm .git")
            shutil.rmtree(".git")
            os.chdir(tmpdir)
            print("package orig.tar.gz")
            subprocess.check_call(["tar", "czf", orig_file,
                                   package_name_version, '--exclude="debian"'])
            os.chdir(os.path.join(tmpdir, package_name_version))
            print("remove old changelog")
            os.remove("debian/changelog")
            print("call dch")
            subprocess.check_call(
                ["dch", "-v",
                 "{0}{1}".format(package_version, version_suffix),
                 "Autobuild - {}".format(commit_id),
                 build.git_url,
                 "--create",
                 "--distribution={}".format(build.release),
                 "-u", build.urgency,
                 "--package", build.name
                 ],
                env=os.environ,
                stdout=logfile, stderr=errorfile)
            print("call debuild")
            gpgkey = ""
            if config.gpgkey:
                gpgkey = "-k{}".format(config.gpgkey)
            subprocess.check_call(
                "debuild -S -sa {}".format(gpgkey),
                env=os.environ, shell=True,
                stdout=logfile, stderr=errorfile)
            os.chdir(tmpdir)
            print("upload package")
            if config.dryrun:
                print("skipped (dry run)")
            else:
                subprocess.check_call(
                    ["dput", ppa, changes_file],
                    stdout=logfile, stderr=errorfile)

        except Exception as e:
            #logging.exception(e)
            # add exception to errorfile?
            print(e)
            print(sys.exc_info()[0])

        finally:
            print("OUTPUT:")
            # TODO
            # mail output of build.sh to build.pusher_email
            if logfile:
                logfile.seek(0)
                print(logfile.read().decode())
                logfile.close()
            if errorfile:
                errorfile.seek(0)
                print(errorfile.read().decode())
                errorfile.close()

            # cleanup
            shutil.rmtree(tmpdir)
        return


def sighandler(num, frame):
    if num == signal.SIGTERM:
        print("TERM: exiting")
        sys.exit(0)


def main():
    global config
    global server
    config = Config()
    pp = pprint.PrettyPrinter()
    pp.pprint(vars(config))
    server = ThreadedHTTPServer(('', config.server_port), MyHandler)
    server.serve_forever()


if __name__ == '__main__':
    print("GitHub-Launchpad-BuildServer started with PID ", os.getpid())
    signal.signal(signal.SIGTERM, sighandler)
    main()
