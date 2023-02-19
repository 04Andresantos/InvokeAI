#!/usr/bin/env python
# Copyright (c) 2022 Lincoln D. Stein (https://github.com/lstein)
# Before running stable-diffusion on an internet-isolated machine,
# run this script from one with internet connectivity. The
# two machines must share a common .cache directory.

'''
This is the npyscreen frontend to the model installation application.
The work is actually done in backend code in model_install_backend.py.
'''

import argparse
import curses
import os
import sys
import traceback
from argparse import Namespace
from typing import List

import npyscreen
import torch
from datetime import datetime
from pathlib import Path
from npyscreen import widget
from omegaconf import OmegaConf

from ..devices import choose_precision, choose_torch_device
from ..globals import Globals
from .widgets import MultiSelectColumns, TextBox
from .model_install_backend import (Dataset_path, default_config_file,
                                    install_requested_models,
                                    default_dataset, get_root
                                    )

class addModelsForm(npyscreen.FormMultiPageAction):
    def __init__(self, parentApp, name):
        self.initial_models = OmegaConf.load(Dataset_path)
        try:
            self.existing_models = OmegaConf.load(default_config_file())
        except:
            self.existing_models = dict()
        self.starter_model_list = [
            x for x in list(self.initial_models.keys()) if x not in self.existing_models
        ]
        self.installed_models=dict()
        super().__init__(parentApp, name)

    def create(self):
        window_height, window_width = curses.initscr().getmaxyx()
        starter_model_labels = self._get_starter_model_labels()
        recommended_models = [
            x
            for x in self.starter_model_list
            if self.initial_models[x].get("recommended", False)
        ]
        self.installed_models = sorted(
            [
                x for x in list(self.initial_models.keys()) if x in self.existing_models
            ]
        )
        self.nextrely -= 1
        self.add_widget_intelligent(
            npyscreen.FixedText,
            value='Use ctrl-N and ctrl-P to move to the <N>ext and <P>revious fields,',
            editable=False,
        )
        self.add_widget_intelligent(
            npyscreen.FixedText,
            value='cursor arrows to make a selection, and space to toggle checkboxes.',
            editable=False,
        )
        self.nextrely += 1
        if len(self.installed_models) > 0:
            self.add_widget_intelligent(
                npyscreen.TitleFixedText,
                name="== INSTALLED STARTER MODELS ==",
                value="Currently installed starter models. Uncheck to delete:",
                begin_entry_at=2,
                editable=False,
                color="CONTROL",
            )
            columns = self._get_columns()
            self.previously_installed_models = self.add_widget_intelligent(
                MultiSelectColumns,
                columns=columns,
                values=self.installed_models,
                value=[x for x in range(0,len(self.installed_models))],
                max_height=2+len(self.installed_models) // columns,
                relx = 4,
                slow_scroll=True,
                scroll_exit = True,
            )
            self.purge_deleted = self.add_widget_intelligent(
                npyscreen.Checkbox,
                name='Purge deleted models from disk',
                value=False,
                scroll_exit=True
            )
        self.add_widget_intelligent(
            npyscreen.TitleFixedText,
            name="== STARTER MODELS (recommended ones selected) ==",
            value="Select from a starter set of Stable Diffusion models from HuggingFace:",
            begin_entry_at=2,
            editable=False,
            color="CONTROL",
        )
        self.nextrely -= 1
        # if user has already installed some initial models, then don't patronize them
        # by showing more recommendations
        show_recommended = self.installed_models is None or len(self.installed_models)==0
        self.models_selected = self.add_widget_intelligent(
            npyscreen.MultiSelect,
            name="Install Starter Models",
            values=starter_model_labels,
            value=[
                self.starter_model_list.index(x)
                for x in self.starter_model_list
                if show_recommended and x in recommended_models
            ],
            max_height=len(starter_model_labels) + 1,
            relx = 4,
            scroll_exit=True,
        )
        for line in [
                '== IMPORT LOCAL AND REMOTE MODELS ==',
                'Enter URLs, file paths, or HuggingFace diffusers repository IDs separated by spaces.',
                'Use control-V or shift-control-V to paste:'
        ]:
            self.add_widget_intelligent(
                npyscreen.TitleText,
                name=line,
                editable=False,
                color="CONTROL",
            )
            self.nextrely -= 1
        self.import_model_paths = self.add_widget_intelligent(
            TextBox,
            max_height=8,
            scroll_exit=True,
            editable=True,
            relx=4
        )
        self.nextrely += 1
        self.show_directory_fields= self.add_widget_intelligent(
            npyscreen.FormControlCheckbox,
            name='Select a directory for models to import',
            value=False,
        )
        self.autoload_directory = self.add_widget_intelligent(
            npyscreen.TitleFilename,
            name='Directory (<tab> autocompletes):',
            select_dir=True,
            must_exist=True,
            use_two_lines=False,
            labelColor='DANGER',
            begin_entry_at=34,
            scroll_exit=True,
        )
        self.autoscan_on_startup = self.add_widget_intelligent(
            npyscreen.Checkbox,
            name='Scan this directory each time InvokeAI starts for new models to import',
            value=False,
            relx = 4,
            scroll_exit=True,
        )
        self.convert_models = self.add_widget_intelligent(
            npyscreen.TitleSelectOne,
            name='== CONVERT IMPORTED MODELS INTO DIFFUSERS==',
            values=['Keep original format','Convert to diffusers'],
            value=0,
            begin_entry_at=4,
            scroll_exit=True,
        )
        for i in [self.autoload_directory,self.autoscan_on_startup]:
            self.show_directory_fields.addVisibleWhenSelected(i)

    def resize(self):
        super().resize()
        self.models_selected.values = self._get_starter_model_labels()
        
    def _get_starter_model_labels(self)->List[str]:
        window_height, window_width = curses.initscr().getmaxyx()
        label_width = 25
        checkbox_width = 4
        spacing_width = 2
        description_width = window_width - label_width - checkbox_width - spacing_width
        im = self.initial_models
        names = self.starter_model_list
        descriptions = [im[x].description [0:description_width-3]+'...'
                        if len(im[x].description) > description_width
                        else im[x].description
                        for x in names]
        return [
            f"%-{label_width}s %s" % (names[x], descriptions[x]) for x in range(0,len(names))
        ]

    def _get_columns(self)->int:
        window_height, window_width = curses.initscr().getmaxyx()
        return 4 if window_width > 240 else 3 if window_width>160 else 2 if window_width>80 else 1

    def on_ok(self):
        self.parentApp.setNextForm(None)
        self.editing = False
        self.parentApp.user_cancelled = False
        self.marshall_arguments()

    def on_cancel(self):
        self.parentApp.setNextForm(None)
        self.parentApp.user_cancelled = True
        self.editing = False

    def marshall_arguments(self):
        '''
        Assemble arguments and store as attributes of the application:
        .starter_models: dict of model names to install from INITIAL_CONFIGURE.yaml
                         True  => Install
                         False => Remove
        .scan_directory: Path to a directory of models to scan and import
        .autoscan_on_startup:  True if invokeai should scan and import at startup time
        .import_model_paths:   list of URLs, repo_ids and file paths to import
        .convert_to_diffusers: if True, convert legacy checkpoints into diffusers
        '''
        # we're using a global here rather than storing the result in the parentapp
        # due to some bug in npyscreen that is causing attributes to be lost
        selections = self.parentApp.user_selections

        # starter models to install/remove
        starter_models = dict(map(lambda x: (self.starter_model_list[x], True), self.models_selected.value))
        selections.purge_deleted_models=False
        if hasattr(self,'previously_installed_models'):
            unchecked = [
                self.previously_installed_models.values[x]
                for x in range(0,len(self.previously_installed_models.values))
                if x not in self.previously_installed_models.value
            ]
            starter_models.update(
                map(lambda x: (x, False), unchecked)
            )
            selections.purge_deleted_models = self.purge_deleted.value
        selections.starter_models=starter_models

        # load directory and whether to scan on startup
        if self.show_directory_fields.value:
            selections.scan_directory = self.autoload_directory.value
            selections.autoscan_on_startup = self.autoscan_on_startup.value
        else:
            selections.scan_directory = None
            selections.autoscan_on_startup = False

        # URLs and the like
        selections.import_model_paths = self.import_model_paths.value.split()
        selections.convert_to_diffusers = self.convert_models.value[0] == 1
            
class AddModelApplication(npyscreen.NPSAppManaged):
    def __init__(self):
        super().__init__()
        self.user_cancelled = False
        self.models_to_install = None
        self.user_selections = Namespace(
            starter_models = None,
            purge_deleted_models = False,
            scan_directory = None,
            autoscan_on_startup = None,
            import_model_paths = None,
            convert_to_diffusers = None
        )

    def onStart(self):
        npyscreen.setTheme(npyscreen.Themes.DefaultTheme)
        self.main_form = self.addForm(
            "MAIN",
            addModelsForm,
            name="Add/Remove Models",
        )

# --------------------------------------------------------
def process_and_execute(opt: Namespace, selections: Namespace):
    models_to_remove  = [x for x in selections.starter_models if not selections.starter_models[x]]
    models_to_install = [x for x in selections.starter_models if selections.starter_models[x]]
    directory_to_scan = selections.scan_directory
    scan_at_startup = selections.autoscan_on_startup
    potential_models_to_install = selections.import_model_paths
    convert_to_diffusers = selections.convert_to_diffusers

    install_requested_models(
        install_initial_models = models_to_install,
        remove_models = models_to_remove,
        scan_directory = Path(directory_to_scan) if directory_to_scan else None,
        external_models = potential_models_to_install,
        scan_at_startup = scan_at_startup,
        convert_to_diffusers = convert_to_diffusers,
        precision = 'float32' if opt.full_precision else choose_precision(torch.device(choose_torch_device())),
        purge_deleted = selections.purge_deleted_models,
        config_file_path = Path(opt.config_file) if opt.config_file else None,
    )
                        
# --------------------------------------------------------
def select_and_download_models(opt: Namespace):
    if opt.default_only:
        models_to_install = default_dataset()
        install_requested_models(
            install_initial_models = models_to_install,
            precision = 'float32' if opt.full_precision else choose_precision(torch.device(choose_torch_device())),
        )
    else:
        installApp = AddModelApplication()
        installApp.run()

        if not installApp.user_cancelled:
            process_and_execute(opt, installApp.user_selections)

# -------------------------------------
def main():
    parser = argparse.ArgumentParser(description="InvokeAI model downloader")
    parser.add_argument(
        "--full-precision",
        dest="full_precision",
        action=argparse.BooleanOptionalAction,
        type=bool,
        default=False,
        help="use 32-bit weights instead of faster 16-bit weights",
    )
    parser.add_argument(
        "--yes",
        "-y",
        dest="yes_to_all",
        action="store_true",
        help='answer "yes" to all prompts',
    )
    parser.add_argument(
        "--default_only",
        action="store_true",
        help="only install the default model",
    )
    parser.add_argument(
        "--config_file",
        "-c",
        dest="config_file",
        type=str,
        default=None,
        help="path to configuration file to create",
    )
    parser.add_argument(
        "--root_dir",
        dest="root",
        type=str,
        default=None,
        help="path to root of install directory",
    )
    opt = parser.parse_args()

    # setting a global here
    Globals.root = os.path.expanduser(get_root(opt.root) or "")

    try:
        select_and_download_models(opt)
    except AssertionError as e:
        print(str(e))
        sys.exit(-1)
    except KeyboardInterrupt:
        print("\nGoodbye! Come back soon.")
    except (widget.NotEnoughSpaceForWidget, Exception) as e:
        if str(e).startswith("Height of 1 allocated"):
            print(
                "** Insufficient vertical space for the interface. Please make your window taller and try again"
            )
        elif str(e).startswith('addwstr'):
            print(
                '** Insufficient horizontal space for the interface. Please make your window wider and try again.'
            )
        else:
            print(f"** An error has occurred: {str(e)}")
            traceback.print_exc()
        sys.exit(-1)

# -------------------------------------
if __name__ == "__main__":
    main()
