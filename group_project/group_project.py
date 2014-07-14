# -*- coding: utf-8 -*-
#

# Imports ###########################################################

import logging
import textwrap
import json
import webob
from lxml import etree
from xml.etree import ElementTree as ET
from pkg_resources import resource_filename

from django.conf import settings
from django.utils.translation import ugettext as _

from xblock.core import XBlock
from xblock.fields import Scope, String, Dict, Float
from xblock.fragment import Fragment

from StringIO import StringIO

from .utils import render_template, AttrDict, load_resource

from .group_activity import GroupActivity
from .project_api import ProjectAPI
from .upload_file import UploadFile


# Globals ###########################################################

log = logging.getLogger(__name__)


# Classes ###########################################################

class GroupProjectBlock(XBlock):

    """
    XBlock providing a group activity project for a group of students to collaborate upon
    """
    display_name = String(
        display_name="Display Name",
        help="This name appears in the horizontal navigation at the top of the page.",
        scope=Scope.settings,
        default="Group Project"
    )

    weight = Float(
        display_name="Weight",
        help="This is the maximum score that the user receives when he/she successfully completes the problem",
        scope=Scope.settings,
        default=1
    )

    item_state = Dict(
        help="JSON payload for assessment values",
        scope=Scope.user_state
    )

    with open(resource_filename(__name__, 'res/default.xml'), "r") as default_xml_file:
        default_xml = default_xml_file.read()

    data = String(
        display_name="",
        help="XML contents to display for this module",
        scope=Scope.content,
        default=textwrap.dedent(default_xml)
    )

    has_score = True

    _project_api = None

    @property
    def project_api(self):
        if self._project_api is None:
            api_server = "http://127.0.0.1:8000"
            if hasattr(settings, 'API_LOOPBACK_ADDRESS'):
                api_server = settings.API_LOOPBACK_ADDRESS
            self._project_api = ProjectAPI(api_server)
        return self._project_api

    @property
    def user_id(self):
        try:
            return self.xmodule_runtime.get_real_user(self.xmodule_runtime.anonymous_student_id).id
        except:
            return None

    _workgroup = None

    @property
    def workgroup(self):
        if self._workgroup is None:
            self._workgroup = self.project_api.get_user_workgroup_for_course(
                self.user_id,
                self.xmodule_runtime.course_id
            )

        return self._workgroup

    def student_view(self, context):
        """
        Player view, displayed to the student
        """
        user_id = self.user_id
        group_activity = GroupActivity.import_xml_string(self.data)

        try:
            group_activity.update_submission_data(
                self.project_api.get_latest_workgroup_submissions_by_id(self.workgroup["id"])
            )
        except:
            pass

        try:
            team_members = [self.project_api.get_user_details(tm["id"]) for tm in self.workgroup["users"] if user_id != int(tm["id"])]
        except:
            team_members = []

        try:
            assess_groups = self.project_api.get_workgroups_to_review(user_id)
        except:
            assess_groups = []

        context = {
            "group_activity": group_activity,
            "team_members": json.dumps(team_members),
            "assess_groups": json.dumps(assess_groups),
        }

        fragment = Fragment()
        fragment.add_content(
            render_template('/templates/html/group_project.html', context))
        fragment.add_css(load_resource('public/css/group_project.css'))

        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/vendor/jquery.ui.widget.js'))
        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/vendor/jquery.fileupload.js'))
        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/vendor/jquery.iframe-transport.js'))

        fragment.add_javascript(load_resource('public/js/group_project.js'))

        fragment.initialize_js('GroupProjectBlock')

        return fragment

    def studio_view(self, context):
        """
        Editing view in Studio
        """
        fragment = Fragment()
        fragment.add_content(render_template('/templates/html/group_project_edit.html', {
            'self': self,
        }))

        fragment.add_javascript(
            load_resource('public/js/group_project_edit.js'))

        fragment.initialize_js('GroupProjectEditBlock')

        return fragment

    @XBlock.json_handler
    def studio_submit(self, submissions, suffix=''):

        self.display_name = submissions['display_name']
        xml_content = submissions['data']
        max_score = submissions['max_score']

        if not max_score:
            # empty = default
            max_score = 100
        else:
            try:
                # not an integer, then default
                max_score = int(max_score)
            except:
                max_score = 100

        self.weight = max_score

        try:
            etree.parse(StringIO(xml_content))
            self.data = xml_content
        except etree.XMLSyntaxError as e:
            return {
                'result': 'error',
                'message': e.message
            }

        return {
            'result': 'success',
        }

    @XBlock.json_handler
    def submit_peer_feedback(self, submissions, suffix=''):
        try:
            peer_id = submissions["peer_id"]
            del submissions["peer_id"]

            # Then something like this needs to happen
            self.project_api.submit_peer_review_items(
                self.xmodule_runtime.anonymous_student_id,
                peer_id,
                self.workgroup['id'],
                submissions
            )

        except Exception as e:
            return {
                'result': 'error',
                'msg': e.message,
            }

        return {
            'result': 'success',
            'msg': _('Thanks for your feedback'),
        }

    @XBlock.json_handler
    def submit_other_group_feedback(self, submissions, suffix=''):
        try:
            group_id = submissions["group_id"]
            del submissions["group_id"]

            self.project_api.submit_workgroup_review_items(
                self.xmodule_runtime.anonymous_student_id,
                group_id,
                self.id,
                submissions
            )

        except Exception as e:
            return {
                'result': 'error',
                'msg': e.message,
            }

        return {
            'result': 'success',
            'msg': _('Thanks for your feedback'),
        }

    @XBlock.handler
    def load_peer_feedback(self, request, suffix=''):

        peer_id = request.GET["peer_id"]
        feedback = self.project_api.get_peer_review_items(
            self.xmodule_runtime.anonymous_student_id,
            peer_id,
            self.workgroup['id']
        )

        # pivot the data to show question -> answer
        results = {pi['question']: pi['answer'] for pi in feedback}

        return webob.response.Response(body=json.dumps(results))

    @XBlock.handler
    def load_other_group_feedback(self, request, suffix=''):

        group_id = request.GET["group_id"]

        feedback = self.project_api.get_workgroup_review_items(
            self.xmodule_runtime.anonymous_student_id,
            group_id,
            self.id
        )

        # pivot the data to show question -> answer
        results = {ri['question']: ri['answer'] for ri in feedback}

        return webob.response.Response(body=json.dumps(results))

    @XBlock.handler
    def load_my_peer_feedback(self, request, suffix=''):

        user_id = self.user_id
        feedback = self.project_api.get_user_peer_review_items(
            user_id,
            self.workgroup['id']
        )

        results = {}
        for item in feedback:
            if item['question'] in results:
                results[item['question']].append(item['answer'])
            else:
                results[item['question']] = [item['answer']]

        return webob.response.Response(body=json.dumps(results))

    @XBlock.handler
    def load_my_group_feedback(self, request, suffix=''):
        workgroup_id = self.workgroup['id']
        feedback = self.project_api.get_workgroup_review_items_for_group(
            workgroup_id,
            self.id
        )

        results = {}
        for item in feedback:
            if item['question'] in results:
                results[item['question']].append(item['answer'])
            else:
                results[item['question']] = [item['answer']]

        results["final_grade"] = [
            self.project_api.get_group_grade(workgroup_id)]

        return webob.response.Response(body=json.dumps(results))

    @XBlock.handler
    def upload_submission(self, request, suffix=''):
        response_data = {"message": _("File(s) successfully submitted")}
        try:
            group_activity = GroupActivity.import_xml_string(self.data)

            context = {
                "user_id": self.user_id,
                "group_id": self.workgroup['id'],
                "project_api": self.project_api,
                "course_id": self.xmodule_runtime.course_id
            }

            upload_files = [UploadFile(request.params[s.id].file, s.id, context)
                            for s in group_activity.submissions if s.id in request.params]

            # Save the files first
            for uf in upload_files:
                uf.save_file()

            # They all got saved... note the submissions
            for uf in upload_files:
                uf.submit()

            response_data.update({uf.submission_id : uf.file_url for uf in upload_files})

        except Exception as e:
            response_data.update({"message": _("Error uploading file(s) - {}").format(e.message)})

        return webob.response.Response(body=json.dumps(response_data))

    @XBlock.handler
    def other_submission_links(self, request, suffix=''):
        group_activity = GroupActivity.import_xml_string(self.data)
        group_id = request.GET["group_id"]

        group_activity.update_submission_data(
            self.project_api.get_latest_workgroup_submissions_by_id(group_id)
        )
        html_output = render_template('/templates/html/submission_links.html', {"group_activity": group_activity})

        return webob.response.Response(body=json.dumps({"html":html_output}))

    @XBlock.handler
    def refresh_submission_links(self, request, suffix=''):
        group_activity = GroupActivity.import_xml_string(self.data)

        group_activity.update_submission_data(
            self.project_api.get_latest_workgroup_submissions_by_id(self.workgroup['id'])
        )
        html_output = render_template('/templates/html/submission_links.html', {"group_activity": group_activity})

        return webob.response.Response(body=json.dumps({"html":html_output}))
