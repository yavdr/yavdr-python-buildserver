from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import subprocess

port = 8080
real_owner = "flensrocker" # "yavdr"
real_url = "git://github.com/flensrocker/" # "git://github.com/yavdr/"
build_script = "build.sh"

class BuildHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        try:
            bdata = self.rfile.read(int(self.headers['Content-Length']))
            sdata = bdata.decode("utf-8")
            jdata = json.loads(sdata)

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

            stage = "unstable" # "testing", "stable"
            if branch.startswith("stable-"):
                stage = "stable"
            elif branch.startswith("testing-"):
                stage = "testing"

            section = "" # "vdr", "yavdr", "main" (not used)
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
            print("git-url: ", git_url)
            print("stage:   ", stage)
            print("section: ", section)
            print("dist:    ", dist)
            print("urgency: ", urgency)

            # TODO
            # call build.sh - why in background?
            try:
                output = subprocess.check_output([build_script,
                                                  name,
                                                  branch,
                                                  dist,
                                                  stage, 
                                                  git_url,
                                                  urgency],
                                                  stderr=subprocess.STDOUT, 
                                                  shell=True)
                    
                print(output)
            except subprocess.CalledProcessError:
                print("calling buildscript failed:")
                print(output)
            except Exception as e:
                print(e)
            # mail output of build.sh to pusher_email

        except Exception as ex:
            print("error", ex)

        self.send_response(200)
        self.end_headers()

        return

if __name__ == '__main__':
    server = HTTPServer(('', port), BuildHandler)
    server.serve_forever()
