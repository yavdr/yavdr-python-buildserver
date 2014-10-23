#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from socketserver import ThreadingMixIn
import argparse
import hashlib
import hmac
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
#import threading

port = 8080
real_owner = "seahawk1986"  # "yavdr"
real_url = "git://github.com/seahawk1986/"  # "git://github.com/yavdr/"
debemail = "seahawk1986@gmx.de"
debfullname = "Alexander Grothe"
ppa_owner = "yavdr"

# set up environment variables
HOOK_SECRET_KEY = os.environb[b'HOOK_SECRET_KEY']
os.environ['DEBEMAIL'] = debemail
os.environ['DEBFULLNAME'] = debfullname
os.environ['EDITOR'] = 'true'


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""


class GithubHookHandler(BaseHTTPRequestHandler):
    """Base class for webhook handlers.

    Subclass it and implement 'handle_payload'.
    """
    def _validate_signature(self, data):
        sha_name, signature = self.headers['X-Hub-Signature'].split('=')
        if sha_name != 'sha1':
            return False

        # HMAC requires its key to be bytes, but data is strings.
        mac = hmac.new(HOOK_SECRET_KEY, msg=data, digestmod=hashlib.sha1)
        return hmac.compare_digest(mac.hexdigest(), signature)

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
        jdata = json_payload

        pusher = jdata["pusher"]["name"]
        pusher_email = jdata["pusher"]["email"]
        owner = jdata["repository"]["owner"]["name"]
        name = jdata["repository"]["name"]
        git_url = jdata["repository"]["git_url"]
        branch = jdata["ref"]

        if owner != real_owner:
            raise Exception("wrong owner")
        if not git_url.startswith(real_url):
            raise Exception("wrong repository")
        if not branch.startswith("refs/heads/"):
            raise Exception("unknown branch")

        branch = branch[11:]

        stage = "unstable"  # "testing", "stable"
        if branch.startswith("stable-"):
            stage = "stable"
        elif branch.startswith("testing-"):
            stage = "testing"

        section = ""  # "vdr", "yavdr", "main" (not used)
        if name.startswith("vdr-"):
            section = "vdr"
        elif name.startswith("yavdr-"):
            section = "yavdr"
        else:
            raise Exception("unknown section")

        dist = "trusty"
        if branch.endswith("-0.5"):
            dist = "precise"
        elif branch.endswith("-0.6"):
            dist = "trusty"

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

        version_suffix = "-0yavdr0~{}".format(dist)
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
            PPA_OWNER=ppa_owner, LPREPO=lprepo)
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

if __name__ == '__main__':
    argparser = argparse.ArgumentParser(description='Github hook handler')
    #argparser.add_argument('port', type=int, help='TCP port to listen on')
    argparser.add_argument('-c', '--config', type=str,
                           help='configuration file')
    args = argparser.parse_args()
    server = ThreadedHTTPServer(('', port), MyHandler)
    server.serve_forever()
