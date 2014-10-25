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
import subprocess
import sys
import tempfile
#import threading

config = None

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

    def get_setting(self, category, setting, default=None):
        if self.configparser.has_option(category, setting):
            return self.configparser.get(category, setting)
        else:
            return default

    def get_config(self):
        self.server_port = int(self.get_setting("Server", "port", "8180"))
        self.launchpad_owner = self.get_setting("Launchpad", "owner", "yavdr")
        self.section_mapping = ast.literal_eval(self.get_setting("Launchpad", "section_mapping", {'vdr-': 'vdr', 'yavdr-': 'yavdr'}))
        self.github_user = self.get_setting("Github", "user", "yavdr")
        self.github_baseurl = self.get_setting("Github", "baseurl", "git://github.com/yavdr/")
        self.master_dist = self.get_setting("Mapping", "master_dist", "trusty")
        self.release_mapping = ast.literal_eval(self.get_setting("Mapping", "release_mapping", {'-0.5': 'precise', '-0.6': 'trusty'}))
        self.stage_mapping = ast.literal_eval(self.get_setting("Mapping", "stage_mapping", {'master': 'unstable', 'stable-': 'stable', 'testing-' : 'testing'}))
        self.debfullname = self.get_setting("Debian", "fullname", "yaVDR Release-Team")
        self.debemail = self.get_setting("Debian", "email", "release@yavdr.org")
        self.version_suffix = self.get_setting("Debian", "version_suffix", "-0yavdr0~{dist}")

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

        payload = json.loads(post_data.decode('utf-8'))
        self.send_response(200)
        self.handle_payload(payload)


class MyHandler(GithubHookHandler):
    def handle_payload(self, json_payload):
        pusher = json_payload["pusher"]["name"]
        pusher_email = json_payload["pusher"]["email"]
        owner = json_payload["repository"]["owner"]["name"]
        name = json_payload["repository"]["name"]
        git_url = json_payload["repository"]["git_url"]
        branch = json_payload["ref"]

        if owner != config.github_user:
            raise Exception("wrong owner")
        if not git_url.startswith(config.github_baseurl):
            raise Exception("wrong repository")
        if not branch.startswith("refs/heads/"):
            raise Exception("unknown branch")

        branch = branch[11:]

        stage = ""
        for k, v in config.stage_mapping.items():
            if branch.startswith(k):
                stage = v
        if stage == "":
            raise Exception("unknown stage")

        dist = config.master_dist
        for k, v in config.release_mapping.items():
            if branch.endswith(k):
                dist = v

        section = ""
        for k, v in config.section_mapping.items():
            print("name: ", name, "; key: ", k, "; value: ", v)
            if name.startswith(k):
                section = v
        if section == "":
            raise Exception("unknown section")

        urgency = "medium"

        print("repo:    ", name)
        print("branch:  ", branch)
        print("owner:   ", owner)
        print("pusher:  ", pusher)
        print("pusher-m:", pusher_email)
        print("git_url: ", git_url)
        print("stage:   ", stage)
        print("section: ", section)
        print("dist:    ", dist)
        print("urgency: ", urgency)

        version_suffix = config.version_suffix.replace("{dist}", dist)
        date = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        print("date:    ", date)
        if section == "main":
            lprepo = "main"
        else:
            lprepo = "{STAGE}-{SECTION}".format(STAGE=stage, SECTION=section)
        print("lprepo:  ", lprepo)

        package_version = "{DATE}{STAGE}".format(DATE=date, STAGE=stage)
        package_name_version = "{PACKAGE_NAME}_{PACKAGE_VERSION}".format(
            PACKAGE_NAME=name, PACKAGE_VERSION=package_version)
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
            tmpdir = tempfile.mkdtemp(suffix=name)
            os.chdir(tmpdir)

            # log the output to files
            logfile = open('build.log', 'w+b')
            errorfile = open('error.log', 'w+b')

            print("checkout sourcecode")
            subprocess.check_call(["git", "clone", "-b", branch, git_url,
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
                 git_url,
                 "--create",
                 "--distribution={}".format(dist),
                 "-u", urgency,
                 "--package", name
                 ],
                env=os.environ,
                stdout=logfile, stderr=errorfile)
            print("call debuild")
            subprocess.check_call(
                "debuild -S -sa",
                env=os.environ, shell=True,
                stdout=logfile, stderr=errorfile)
            os.chdir(tmpdir)
            print("upload package")
            subprocess.check_call(
                ["dput", ppa, changes_file],
                stdout=logfile, stderr=errorfile)

        except Exception as e:
            #logging.exception(e)
            print(e)
            print(sys.exc_info()[0])

        finally:
            print("OUTPUT:")
            # TODO
            # mail output of build.sh to pusher_email
            logfile.seek(0)
            print(logfile.read())
            logfile.close()
            errorfile.seek(0)
            print(errorfile.read())
            errorfile.close()

            # cleanup
            shutil.rmtree(tmpdir)
        return

def main():
    global config
    config = Config()
    pp = pprint.PrettyPrinter()
    pp.pprint(vars(config))
    server = ThreadedHTTPServer(('', config.server_port), MyHandler)
    server.serve_forever()

if __name__ == '__main__':
    main()
