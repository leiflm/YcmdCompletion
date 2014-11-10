# -*- coding: utf8 -*-

from .ycmd import http_client
from base64 import b64decode
from json import loads
from threading import Thread
import os.path
import sublime
import sublime_plugin

PACKAGE_NAME = os.path.splitext(os.path.basename(os.path.dirname(__file__)))[0]
ERROR_MARKER_IMG = 'Packages/{}/marker.png'.format(PACKAGE_NAME)
SETTINGS_NAME = "{}.sublime-settings".format(PACKAGE_NAME)
SETTINGS_PATH = "${packages}/User/" + SETTINGS_NAME

##                    ##
#  MESSAGES TEMPLATES  #
##                    ##
COMPLETION_ERROR_MSG = "[Ycmd][Completion] Error {}"
COMPLETION_NOT_AVAILABLE_MSG = "[Ycmd] No completion available"
ERROR_MESSAGE_TEMPLATE = "[{kind}] {text}"
GET_PATH_ERROR_MSG = "[Ycmd][Path] Failed to replace '{}' -> '{}'"
NO_HMAC_MESSAGE = "[Ycmd] You should generate HMAC throug the menu before using plugin"
NOTIFY_ERROR_MSG = "[Ycmd][Notify] Error {}"
PRINT_ERROR_MESSAGE_TEMPLATE = "[Ycmd] > {} ({},{})"


def open_user_settings():
    sublime.active_window().run_command('open_file', {'file': SETTINGS_PATH})


def active_view():
    return sublime.active_window().active_view()


def read_settings():
    s = sublime.load_settings(SETTINGS_NAME)
    settings = dict()
    settings["server"] = s.get("ycmd_server", "http://127.0.0.1")
    settings["port"] = s.get("ycmd_port", 8080)
    settings["hmac"] = s.get("HMAC", '')
    if not settings["hmac"]:
        sublime.status_message(NO_HMAC_MESSAGE)
    else:
        settings["hmac"] = b64decode(settings["hmac"].encode('utf-8'))
    settings["replace_file_path"] = (None, None)
    replace = s.get("ycmd_filepath_replace", {})
    if replace:
        settings["replace_file_path"] = (replace["from"], replace["to"])
    return settings


def is_cpp(view):
    '''Determine if the given view location is c++ code'''
    try:
        return view.match_selector(view.sel()[0].begin(), 'source.c++')
    except:
        return False


def get_selected_pos(view):
    try:
        return view.rowcol(view.sel()[0].end())
    except:
        return None


def get_file_path(filepath=None):
    ''' Turns filepath to it's modified variant (replace prefix according to settings).
        If filepath is None, trying to get current filepath, opened in active view.
    '''
    if not filepath:
        filepath = active_view().file_name()
    if not filepath:
        filepath = 'tmpfile.cpp'
    from_prefix, to_prefix = read_settings()["replace_file_path"]
    if from_prefix and to_prefix:
        try:
            filepath = filepath.replace(from_prefix, to_prefix)
        except:
            sublime.status_message(GET_PATH_ERROR_MSG.format(from_prefix, to_prefix))
    return filepath


def notify_func(filepath, content, callback):
    settings = read_settings()
    cli = http_client.YcmdClient(settings["server"], settings["port"], settings["hmac"])
    try:
        data = http_client.PrepareForNewFile(cli, filepath, content)
    except Exception as e:
        print(NOTIFY_ERROR_MSG.format(e))
        return
    if callback:
        callback(data)


def complete_func(filepath, row, col, content, error_cb, data_cb):
    settings = read_settings()
    cli = http_client.YcmdClient(settings["server"], settings["port"], settings["hmac"])
    notify_func(filepath, content, error_cb)
    try:
        data = http_client.CppSemanticCompletionResults(cli, filepath,
                                                        row + 1, col + 1,
                                                        content)
    except Exception as e:
        print(COMPLETION_ERROR_MSG.format(e))
        sublime.status_message(COMPLETION_NOT_AVAILABLE_MSG)
        return
    if data_cb:
        data_cb(data)


class YcmdCreateHmacPairCommand(sublime_plugin.WindowCommand):
    def run(self):
        HMAC_b64 = http_client.YcmdClient.GenerateHMAC()[0]
        s = sublime.load_settings(SETTINGS_NAME)
        print("HMAC:", HMAC_b64.decode('utf-8'))
        s.set('HMAC', HMAC_b64.decode('utf-8'))
        sublime.save_settings(SETTINGS_NAME)
        open_user_settings()


class YcmdCompletionEventListener(sublime_plugin.EventListener):

    completions = []
    ready_from_defer = False
    view_cache = dict()
    view_line = dict()

    def on_selection_modified_async(self, view):
        if not is_cpp(view) or view.is_scratch():
            return
        self.update_statusbar(view)

    def on_load_async(self, view):
        '''Called when the file is finished loading'''
        if not is_cpp(view) or view.is_scratch():
            return
        filepath = get_file_path()
        content = view.substr(sublime.Region(0, view.size()))
        t = Thread(None, notify_func, 'NotifyAsync', [filepath, content, self._on_errors])
        t.daemon = True
        t.start()

    def on_post_save_async(self, view):
        if not is_cpp(view) or view.is_scratch():
            return
        self.on_load_async(view)

    def on_pre_close(self, view):
        view_id = view.id()
        if view_id in self.view_line:
            del self.view_line[view_id]
        if view_id in self.view_cache:
            del self.view_cache[view_id]

    def on_query_completions(self, view, prefix, locations):
        '''Sublime Text autocompletion event handler'''
        if not is_cpp(view) or view.is_scratch():
            return

        print("[YCMD] #### START COMPLETION ####")

        if self.ready_from_defer is True:
            cpl = self.completions
            self.completions = []
            self.ready_from_defer = False
            return (cpl, sublime.INHIBIT_WORD_COMPLETIONS | sublime.INHIBIT_EXPLICIT_COMPLETIONS)

        filepath = get_file_path()
        row, col = view.rowcol(locations[0])
        content = view.substr(sublime.Region(0, view.size()))
        t = Thread(None, complete_func, 'CompleteAsync',
                   [filepath, row, col, content, self._on_errors, self._complete])
        t.daemon = True
        t.start()

    def _complete(self, data):
        jsonResp = loads(data)
        proposals = list(self.generate_completion_items(jsonResp['completions']))

        if proposals:
            active_view().run_command("hide_auto_complete")
            self.completions = proposals
            self.ready_from_defer = True
            self._run_auto_complete()
        else:
            sublime.status_message("[Ycmd] No completion available")

    def _run_auto_complete(self):
        active_view().run_command("auto_complete", {
            'disable_auto_insert': True,
            'next_completion_if_showing': False,
            'auto_complete_commit_on_tab': True,
        })

    def _on_errors(self, data):
        data = loads(data)
        filepath = get_file_path()
        self.highlight_problems(active_view(),
                                [_ for _ in data
                                    if get_file_path(_['location']['filepath']) == filepath])
        self.update_statusbar(active_view(), force=True)

    def update_statusbar(self, view, force=False):
        row, col = get_selected_pos(view)
        view_id = view.id()
        text_point = view.text_point(row, col)

        if not force:
            beg, end = self.view_line.get(view_id, (None, None))
            if beg and end and sublime.Region(beg, end).contains(text_point):
                return

        errors_regions = self.view_cache.get(view_id, {}).get(row, {})
        for region, msg in errors_regions.items():
            if sublime.Region(*region).contains(text_point) and msg:
                view.set_status('clang-code-errors', msg)
                self.view_line[view_id] = region
                return
        if view_id in self.view_line:
            del self.view_line[view_id]
        view.erase_status('clang-code-errors')

    def highlight_problems(self, view, problems):
        view.erase_regions('clang-code-errors')
        view_id = view.id()
        view_cache = {}
        regions = []
        for problem in problems:
            lineno = problem['location']['line_num']
            colno = problem['location']['column_num']
            line_regions = view_cache.setdefault(lineno - 1, {})
            message = ERROR_MESSAGE_TEMPLATE.format(**problem)
            print(PRINT_ERROR_MESSAGE_TEMPLATE.format(message, lineno, colno))
            region = view.word(view.text_point(lineno - 1, colno - 1))
            regions.append(region)
            line_regions[(region.a, region.b)] = message
        self.view_cache[view_id] = view_cache
        style = (sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | sublime.DRAW_SQUIGGLY_UNDERLINE)
        view.add_regions('clang-code-errors', regions, 'invalid', ERROR_MARKER_IMG, style)

    def generate_completion_items(self, completions):
        for completion in completions:
            if not 'insertion_text' in completion:
                continue
            insertion = completion['insertion_text']
            yield [completion.get('menu_text', insertion), insertion]
