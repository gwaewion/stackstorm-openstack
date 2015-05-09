#! /usr/bin/env python2.7

import argparse
import logging
import openstackclient.shell as shell
import os
import six
import sys
import yaml


LOG = logging.getLogger(__name__)

# CHANGEME
BASE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'test')

SCRIPT_RELATIVE_PATH = 'src/wrapper.py'

ALL = '*'


class CommandProcessor(object):

    TYPE_LOOKUP = {
        int: 'integer',
        float: 'number',
        bool: 'boolean'
    }

    SKIP_GROUP_NAMES = [
        'output formatters',  # https://github.com/openstack/cliff/blob/master/cliff/display.py#L53
        'table formatter',  # https://github.com/openstack/cliff/blob/master/cliff/formatters/table.py#L22
        'shell formatter',  # https://github.com/openstack/cliff/blob/master/cliff/formatters/shell.py#L14
        'CSV Formatter'  # https://github.com/openstack/cliff/blob/master/cliff/formatters/commaseparated.py#L20
    ]

    def __init__(self, command, entry_point):
        self._command_text = command
        self._command_name = command.replace(' ', '.')
        self._entry_point = entry_point
        self._command_cls = entry_point.load()
        self._skip_groups = []

    def _get_parameter(self, default=None, description=None, type_='string', required=False,
                       immutable=False):
        return {
            'default': default,
            'description': description,
            'type': type_,
            'required': required,
            'immutable': immutable
        } if default is not None else {
            'description': description,
            'type': type_,
            'required': required,
            'immutable': immutable
        }

    def _is_required(self, action, parser):
        if type(action.required) is bool:
            return action.required
        # if is optional then is not required
        if action.option_strings:
            return False
        # positional actions in mutually_exclusive_groups cannot be required.
        for mex_group in parser._mutually_exclusive_groups:
            if action in mex_group._group_actions:
                return False
        return True

    def _get_type(self, action):
        if action.type in self.TYPE_LOOKUP:
            return self.TYPE_LOOKUP[action.type]
        # In all these cases the value that will be stored is know so
        # so we only pick where to append or not.
        if isinstance(action, argparse._StoreTrueAction) or \
           isinstance(action, argparse._StoreFalseAction) or \
           isinstance(action, argparse._AppendConstAction):
            return 'boolean'
        if isinstance(action, argparse._AppendAction):
            return 'array'
        return 'string'

    def _get_default(self, action):
        # special handling for the formatter action. default value of table
        # is no good in this case.
        if action.dest == 'formatter' and 'json' in action.choices:
            return 'json'
        if action.default is not None:
            return action.default
        if isinstance(action, argparse._StoreTrueAction):
            return False
        # For _AppendConstAction so not append by default.
        if isinstance(action, argparse._StoreFalseAction) or \
           isinstance(action, argparse._AppendConstAction):
            return True

    def _setup_skip_groups(self, parser):
        # Add groups to skip group.
        self._skip_groups.extend(
            [g for g in parser._action_groups if g.title in self.SKIP_GROUP_NAMES])
        self._skip_groups.extend(
            [g for g in parser._mutually_exclusive_groups if g.title in self.SKIP_GROUP_NAMES])

    def _test_skip_action(self, action, parser):
        # hack for formatter
        if action.dest == 'formatter' and 'json' in action.choices:
            return False
        # This check is leading to some really mruky issues. Lots of formatting
        # options which might be usef are lost for example. For now helps with
        # simplicity of creating a usable pack.
        for skip_group in self._skip_groups:
            # It is important to use _group_actions to limit to action within a group.
            if action in skip_group._group_actions:
                LOG.debug('\n%s in %s', action, skip_group.title)
                return True
        return False

    def _parse_parameter(self, action, parser):
        if self._test_skip_action(action, parser):
            return None, None
        # param name is from the options string of fully expanded
        usable_options = [x for x in action.option_strings if x.startswith('--')]
        name = usable_options[0][len('--'):] if usable_options else action.dest

        # All positionals outside of a mutually exclusive group are required.
        required = self._is_required(action, parser)

        # type is string if not specified otherwise
        type_ = self._get_type(action)

        # for a few actions default is defined by type(action)
        default = self._get_default(action)

        # Make sure choices are included in the description. Often action.help
        # may not list choices. It is perhaps better if this type were an enum?
        descripton = str(action.help) if not action.choices else \
            '%s (choices: %s)' % (action.help, ', '.join(action.choices))

        return name, self._get_parameter(default=default, description=descripton,
                                         type_=type_, required=required)

    def _parse_parameters(self, parser):
        parameters = {}

        # skip groups should be put in place before
        self._setup_skip_groups(parser)

        for action in parser._actions:
            # value implies that argparse will ignore.
            if action.dest is argparse.SUPPRESS or action.default is argparse.SUPPRESS:
                continue
            name, meta = self._parse_parameter(action, parser)
            if not name and not meta:
                LOG.debug('\033[91mskipping:\033[0m %s', action)
                continue

            # include some extra debug info. Useful if with a single action.
            LOG.debug('\033[92m\033[4m\033[1m%s\033[0m', name)
            LOG.debug('%s\n%s', action, meta)

            parameters[name] = meta

        parameters['ep'] = self._get_parameter(default=repr(self._entry_point), immutable=True)
        parameters['base'] = self._get_parameter(default=self._command_text, immutable=True)
        return parameters

    def __call__(self):
        command = self._command_cls(None, None)
        parser = command.get_parser('autogen')
        parameters = self._parse_parameters(parser)
        LOG.debug('No of parameters %s', len(parameters))
        return {
            'name': self._command_name,
            'runner_type': 'run-python',
            'entry_point': SCRIPT_RELATIVE_PATH,
            'enabled': True,
            'description': self._command_cls.__doc__,
            'parameters': parameters
        }


class MetaDataWriter(object):

    def __init__(self, base_path=BASE_PATH, script_relative_path=SCRIPT_RELATIVE_PATH):
        self._base_path = base_path
        self._script_relative_path = script_relative_path

    def write(self, command):
        metadata_file_path = os.path.join(self._base_path, '%s.%s' % (command['name'], 'yaml'))
        with open(metadata_file_path, 'w') as out:
            out.write(yaml.dump(command, explicit_start=True, default_flow_style=False, indent=4))
        return metadata_file_path


def _setup_shell_app():
    # create app and run help command. This bootstrap the application.
    app = shell.OpenStackShell()
    try:
        # reduce noise - 1
        org_print_message = argparse.ArgumentParser._print_message

        def devnull(obj, message, file):
            pass

        argparse.ArgumentParser._print_message = devnull

        # reduce noise - 2
        stdout = sys.stdout
        stderr = sys.stderr
        app_stdout = app.stdout
        app_stderr = app.stderr

        dev_null = open(os.devnull, 'w')
        sys.stdout = dev_null
        sys.stderr = dev_null
        app.stdout = dev_null
        app.stderr = dev_null

        app.run(['--help'])
    except SystemExit:
        pass
    finally:
        # reassign original stdout and stderr
        sys.stdout = stdout
        sys.stderr = stderr
        app.stdout = app_stdout
        app.stderr = app_stderr
        argparse.ArgumentParser._print_message = org_print_message
    return app


def _get_commands(app):
    return app.command_manager.commands


def _is_command_in_namespace(command, namespace):
    if namespace == ALL:
        return True
    return command.startswith(namespace)


def _process_commands(commands, namespace=ALL, base_write_path=BASE_PATH):
    writer = MetaDataWriter(base_path=base_write_path)
    for command, ep in six.iteritems(commands):
        if not _is_command_in_namespace(command, namespace):
            continue
        writeable_command = CommandProcessor(command, ep)()
        path = writer.write(writeable_command)
        LOG.info('%s : %s ', writeable_command['name'], path)


def _setup_logging(debug=False):
    level = logging.DEBUG if debug else logging.INFO
    LOG.setLevel(level)

    # log to console
    ch = logging.StreamHandler()
    ch.setLevel(level)

    formatter = logging.Formatter('%(message)s')
    ch.setFormatter(formatter)

    LOG.addHandler(ch)


def _get_parsed_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ns', '-n', dest='namespace', default=ALL)
    parser.add_argument('--path', '-p', dest='base_path', default=BASE_PATH)
    parser.add_argument('--debug', '-d', dest='debug', action='store_true')
    return parser.parse_args()


def main():
    args = _get_parsed_args()
    _setup_logging(args.debug)
    app = _setup_shell_app()
    commands = _get_commands(app)
    _process_commands(commands, namespace=args.namespace, base_write_path=args.base_path)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except:
        LOG.exception('autogen stalled.')
