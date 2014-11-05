#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from email.mime.text import MIMEText
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
import os
import shutil
import signal
import smtplib
import subprocess
import sys
import tempfile
import threading

version = "0.1.3"
config = None
server = None


def get_from_args(args, key, default = None):
    if key in args:
        return args[key]
    if default:
        return default
    raise Exception("missing argument {}".format(key))


class Config:
    def __init__(self):
        argparser = argparse.ArgumentParser(description='Github hook handler')
        argparser.add_argument('-c', '--config', action='append', metavar='CONFIG', dest='config', default=None, help='configuration file(s)')
        argparser.add_argument('-b', '--build', action='store_true', dest='build', default=None, help='direct build, don\'t serve')
        argparser.add_argument('--pusher', metavar='PUSHER', dest='pusher', default=None, help='name of the commit pusher')
        argparser.add_argument('--pusher-email', metavar='PUSHEREMAIL', dest='pusher-email', default=None, help='email address of the commit pusher')
        argparser.add_argument('--owner', metavar='OWNER', dest='owner', default=None, help='owner of the git repository')
        argparser.add_argument('--name', metavar='NAME', dest='name', default=None, help='name of the package/repository')
        argparser.add_argument('--git-url', metavar='GITURL', dest='git-url', default=None, help='clone-url of the git repository')
        argparser.add_argument('--branch', metavar='BRANCH', dest='branch', default=None, help='name of the branch to clone')
        argparser.add_argument('--urgency', metavar='URGENCY', dest='urgency', default="medium", help='urgency of the build')
        self.args = vars(argparser.parse_args())
        self.configparser = configparser.SafeConfigParser()
        if "config" in self.args:
            self.configparser.read(self.args["config"])
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
        self.direct_build = self.args["build"]
        self.dryrun = self.get_settingb("Server", "dryrun", False)
        self.server_port = int(self.get_setting("Server", "port", "8180"))
        self.smtp_server = self.get_setting("Server", "smtp_server", None)
        self.smtp_sender = self.get_setting("Server", "smtp_sender", None)
        self.smtp_tls = self.get_settingb("Server", "smtp_tls", False)
        self.smtp_user = self.get_setting("Server", "smtp_user", None)
        self.smtp_password = self.get_setting("Server", "smtp_password", None)
        if not self.smtp_sender:
            self.smtp_server = None
        
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


class Build(threading.Thread):
    def __init__(self, config):
        threading.Thread.__init__(self)
        self.config = config
        self.pusher = ""
        self.pusher_email = ""
        self.owner = ""
        self.name = ""
        self.git_url = ""
        self.branch = ""
        self.stage = ""
        self.release = ""
        self.section = ""
        self.urgency = "medium"
        return

    def run(self):
        self.build()
        return

    def output(self, logfile):
        logfile.write("repo:    {}\n".format(self.name).encode())
        logfile.write("branch:  {}\n".format(self.branch).encode())
        logfile.write("owner:   {}\n".format(self.owner).encode())
        logfile.write("pusher:  {0} <{1}>\n".format(self.pusher, self.pusher_email).encode())
        logfile.write("git_url: {}\n".format(self.git_url).encode())
        logfile.write("stage:   {}\n".format(self.stage).encode())
        logfile.write("section: {}\n".format(self.section).encode())
        logfile.write("release: {}\n".format(self.release).encode())
        logfile.write("urgency: {}\n".format(self.urgency).encode())
        return

    def fromgithub(self, json_payload):
        self.pusher = json_payload["pusher"]["name"]
        self.pusher_email = json_payload["pusher"]["email"]
        self.owner = json_payload["repository"]["owner"]["name"]
        self.name = json_payload["repository"]["name"]
        self.git_url = json_payload["repository"]["git_url"]

        branch = json_payload["ref"]
        if not branch.startswith("refs/heads/"):
            raise Exception("unknown branch")
        self.branch = branch[11:]
        return

    def fromargs(self, args):
        self.pusher = get_from_args(args, "pusher")
        self.pusher_email = get_from_args(args, "pusher-email")
        self.owner = get_from_args(args, "owner", "yavdr")
        self.name = get_from_args(args, "name")
        self.git_url = get_from_args(args, "git-url")
        self.branch = get_from_args(args, "branch", "master")
        self.urgency = get_from_args(args, "urgency", "medium")
        return

    def build(self):
        logfile = None
        package_name_version = None
        try:
            # create a temporary directory and enter it
            tmpdir = tempfile.mkdtemp(suffix=self.name)
            print("build directory: ", tmpdir)
            os.chdir(tmpdir)

            # log the output to files
            logfile = open('build.log', 'w+b')

            if self.owner != self.config.github_owner:
                raise Exception("wrong owner")
            if not self.git_url.startswith(self.config.github_baseurl):
                raise Exception("wrong repository")

            self.stage = self.config.default_stage
            matches = [sta for sta in self.config.stages.keys() if self.branch.startswith(sta)]
            if len(matches) > 0:
                max_length, longest_element = max([(len(x),x) for x in matches])
                self.stage = self.config.stages[longest_element]

            self.release = self.config.default_release
            matches = [rel for rel in self.config.releases.keys() if self.branch.endswith(rel)]
            if len(matches) > 0:
                max_length, longest_element = max([(len(x),x) for x in matches])
                self.release = self.config.releases[longest_element]

            matches = [sec for sec in self.config.sections.keys() if self.name.startswith(sec)]
            if len(matches) == 0:
                raise Exception("unknown section")
            max_length, longest_element = max([(len(x),x) for x in matches])
            self.section = self.config.sections[longest_element]

            self.output(logfile)

            version_suffix = config.version_suffix.replace("{release}", self.release)
            date = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            logfile.write("date:    {}\n".format(date).encode())
            if self.section == "main" and self.section != "unstable":
                lprepo = "main"
            else:
                lprepo = "{STAGE}-{SECTION}".format(STAGE=self.stage, SECTION=self.section)
            logfile.write("lprepo:  {}\n".format(lprepo).encode())

            package_version = "{DATE}{STAGE}".format(DATE=date, STAGE=self.stage)
            package_name_version = "{PACKAGE_NAME}_{PACKAGE_VERSION}".format(
                PACKAGE_NAME=self.name, PACKAGE_VERSION=package_version)
            orig_file = "{PACKAGE_NAME_VERSION}.orig.tar.gz".format(
                PACKAGE_NAME_VERSION=package_name_version)
            changes_file = "{PACKAGE_NAME_VERSION}{VERSION_SUFFIX}_source.changes".format(
                PACKAGE_NAME_VERSION=package_name_version,
                VERSION_SUFFIX=version_suffix)
            ppa = "ppa:{PPA_OWNER}/{LPREPO}".format(
                PPA_OWNER=config.launchpad_owner, LPREPO=lprepo)
            logfile.write("ppa:     {}\n".format(ppa).encode())
            logfile.write("version_suffix: {}\n".format(version_suffix).encode())

            logfile.write("\ncheckout sourcecode\n".encode())
            logfile.flush()
            subprocess.check_call(["git", "clone", "--depth", "1", "-b", self.branch, self.git_url,
                                   package_name_version],
                                   stdout=logfile, stderr=subprocess.STDOUT)
            os.chdir(os.path.join(tmpdir, package_name_version))
            logfile.write("get commit_id\n".encode())
            logfile.flush()
            commit_id = subprocess.check_output(["git", "rev-parse", "HEAD"])
            logfile.write("commit_id: {}\n".format(commit_id).encode())
            os.chdir(tmpdir)
            logfile.write("\npackage orig.tar.gz\n".encode())
            logfile.flush()
            subprocess.check_call(["tar", "czf", orig_file,
                                   package_name_version, '--exclude="debian"', '--exclude=".git"'])
            os.chdir(os.path.join(tmpdir, package_name_version))
            logfile.write("\nremove old changelog\n".encode())
            os.remove("debian/changelog")
            logfile.write("\ncall dch\n".encode())
            logfile.flush()
            subprocess.check_call(
                ["dch", "-v",
                 "{0}{1}".format(package_version, version_suffix),
                 "Autobuild - {}".format(commit_id),
                 self.git_url,
                 "--create",
                 "--distribution={}".format(self.release),
                 "-u", self.urgency,
                 "--package", self.name
                 ],
                env=os.environ,
                stdout=logfile, stderr=subprocess.STDOUT)
            logfile.write("\ncall debuild\n".encode())
            logfile.flush()
            gpgkey = ""
            if self.config.gpgkey:
                gpgkey = "-k{}".format(self.config.gpgkey)
            subprocess.check_call(
                "debuild -S -sa {}".format(gpgkey),
                env=os.environ, shell=True,
                stdout=logfile, stderr=subprocess.STDOUT)
            os.chdir(tmpdir)
            logfile.write("\nupload package\n".encode())
            if self.config.dryrun:
                logfile.write("skipped (dry run)\n".encode())
            else:
                logfile.flush()
                subprocess.check_call(
                    ["dput", "-U", ppa, changes_file],
                    stdout=logfile, stderr=subprocess.STDOUT)

        except Exception as e:
            if logfile:
                logfile.write("{}\n".format(e).encode())
                logfile.write("{}\n".format(sys.exc_info()[0]).encode())
            print(e)
            print(sys.exc_info()[0])

        finally:
            if self.config.smtp_server and self.pusher_email and logfile:
                logfile.seek(0)
                msg = MIMEText(logfile.read().decode())
                logfile.close()
                if package_name_version:
                    msg['Subject'] = "Build-Log for {NAME}".format(NAME=package_name_version)
                else:
                    msg['Subject'] = "an unexpected error occured while building"
                msg['From'] = self.config.smtp_sender
                msg['To'] = self.pusher_email
                s = smtplib.SMTP(self.config.smtp_server)
                if self.config.smtp_tls:
                    s.starttls()
                if self.config.smtp_user and self.config.smtp_password:
                    s.login(self.config.smtp_user, self.config.smtp_password)
                s.send_message(msg)
                s.quit()

            # cleanup
            if self.config.dryrun:
                print("dry run, cleanup after yourself")
            else:
                shutil.rmtree(tmpdir)
        return


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
            try:
                return hmac.compare_digest(mac.hexdigest(), signature)
            except:
                pass
            return mac.hexdigest() == signature
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
        try:
            self.flush_headers()
        except AttributeError:
            pass
        # then handle request, so that no timeout occurs (hopefully)
        payload = json.loads(post_data.decode('utf-8'))
        self.handle_payload(payload)


class MyHandler(GithubHookHandler):
    def handle_payload(self, json_payload):
        try:
            build = Build(config)
            build.fromgithub(json_payload)
            build.start() # runs build.build() in separate thread
        except:
            pass
        return


def sighandler(num, frame):
    if num == signal.SIGTERM:
        print("TERM: exiting")
        sys.exit(0)


def main():
    global config
    global server
    config = Config()
    if config.direct_build:
        build = Build(config)
        build.fromargs(config.args)
        build.build()
    else:
        server = ThreadedHTTPServer(('', config.server_port), MyHandler)
        server.serve_forever()


if __name__ == '__main__':
    print("GitHub-to-Launchpad-BuildServer version {0} started with PID {1}".format(version, os.getpid()))
    signal.signal(signal.SIGTERM, sighandler)
    main()
