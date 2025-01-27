import uuid
from typing import Dict, List, Any, Optional, Union, cast

from prompt_toolkit import ANSI
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.widgets import Frame
from prompt_toolkit.layout.containers import Window, HSplit, VSplit
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.console import Console
import copy

from nbtermix.log import log

ONE_COL: Window = Window(width=1)
ONE_ROW: Window = Window(height=1)
CONSOLE: Optional[Console] = None


def set_console(console: Console):
    global CONSOLE
    CONSOLE = console


def rich_print(
    string: str, console: Optional[Console] = None, style: str = "", end: str = ""
):
    console = console or CONSOLE
    assert console is not None
    with console.capture() as capture:
        console.print(string, style=style, end=end)
    return capture.get()


def get_output_text_and_height(outputs: List[Dict[str, Any]]):
    try:
        text_list = []
        height = 0
        for output in outputs:
            log("-- GET OUTPUT TEXT: " + str(output))
            out_type = output["output_type"]
            # log("-- CELL OUTPUT TYPE : " + str(output["output_type"]))
            if out_type == "stream":
                text = "".join(output["text"])
                height += text.count("\n")
                if output["name"] == "stderr":
                    # TODO: take terminal width into account
                    lines = text.splitlines()
                    lines = [line + " " * (200 - len(line)) for line in lines]
                    text = "\n".join(lines)
                    text = rich_print(text, style="white on red", end="\n")
            elif out_type == "error":
                text = "\n".join(output["traceback"])
                height += text.count("\n")
            elif out_type == "display_data" or out_type == "execute_result":
                # text = "\n".join(output["data"].get("text/plain", ""))
                if "text/plain" in output["data"]:
                    text = "\n".join(output["data"]["text/plain"]) + "\n"
                elif "text/html" in output["data"]:
                    text = "\n".join(output["data"]["text/html"]) + "\n"
                    # from bs4 import BeautifulSoup
                    # soup = BeautifulSoup(text)
                    # text = soup.get_text()
                log("-- OUTPUT EXEC RES: " + text)
                # get("text/plain", ""))
                # text = "\n".join(output["data"].get("text/html", ""))
                height += text.count("\n")
            else:
                continue
            text_list.append(text)
        text_ansi = ANSI("".join(text_list))
    except Exception as e:
        log("-- GET OUTPUT: " + text + str(e))
    return (text_ansi, height)


def empty_cell_json():
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "source": [],
        "outputs": [],
    }


class Cell:

    input: Union[Frame, HSplit]
    output: Window
    json: Dict[str, Any]
    input_prefix: Window
    output_prefix: Window
    input_window: Window
    input_buffer: Buffer
    output_buffer: Buffer
    fold: bool
    vshift: int
    hshift: int
    ext_edit: bool

    def __init__(
        self, notebook, cell_json: Optional[Dict[str, Any]] = None, mode="interactive"
    ):
        try:
            self.vshift = 0
            self.hshift = 0
            self.notebook = notebook
            self.json = cell_json or empty_cell_json()
            self.input_prefix = Window(width=10)
            self.output_prefix = Window(width=10, height=0)
            self.fold = self.notebook.fold
            self.ext_edit = False
            input_text = "".join(self.json["source"])
            if self.json["cell_type"] == "code":
                execution_count = self.json["execution_count"] or " "
                text = rich_print(
                    f"\nIn [{execution_count}]:" + self.fold_tag(),
                    style="green",
                )
                self.input_prefix.content = FormattedTextControl(text=ANSI(text))
                outputs = self.json["outputs"]
                for output in outputs:
                    if "execution_count" in output:
                        text = rich_print(
                            f"Out[{output['execution_count']}]:",
                            style="red",
                        )
                        self.output_prefix.content = FormattedTextControl(
                            text=ANSI(text)
                        )
                        break
            else:
                outputs = []
            output_text, output_height = get_output_text_and_height(outputs)
            self.input_window = Window()
            self.input_buffer = Buffer(on_text_changed=self.input_text_changed)
            self.input_buffer.text = input_text
            self.set_input_readonly(mode)
            if self.json["cell_type"] == "markdown":
                self.input = HSplit(
                    [ONE_ROW, VSplit([ONE_COL, self.input_window]), ONE_ROW]
                )
            else:
                self.input = Frame(self.input_window, style="green")
            self.output = Window(content=FormattedTextControl(text=output_text))
            self.output.height = output_height
            self.output_buffer = Buffer()
            if self.fold:
                self.input_window.height = 1
        except Exception as e:
            print(str(e))

    def fold_tag(self):
        if self.fold is False:
            return " "
        if self.fold is True:
            return "-"

    def get_height(self) -> int:
        input_height = cast(int, self.input_window.height) + 2  # include frame
        output_height = cast(int, self.output.height)
        return input_height + output_height

    def copy(self):
        cell_json = copy.deepcopy(self.json)
        cell = Cell(self.notebook, cell_json=cell_json)
        return cell

    def input_text_changed(self, _=None):
        log("-- INPUT TEXT CHANGED --")
        self.notebook.dirty = True
        self.notebook.quitting = False
        line_nb = self.input_buffer.text.count("\n") + 1
        height_keep = self.input_window.height
        self.input_window.height = line_nb
        if height_keep is not None and line_nb != height_keep:
            # height has changed
            self.notebook.focus(self.notebook.current_cell_idx, update_layout=True)
        # self.exit_cell()
        if self.ext_edit is True:
            log("-- TEXT: " + self.input_buffer.text)
            self.ext_edit = False
            self.notebook.edit_mode = False
            self.update_json()
            self.set_input_readonly()
            self.notebook.focus(self.notebook.current_cell_idx, update_layout=True)

    def set_as_markdown(self):
        prev_cell_type = self.json["cell_type"]
        if prev_cell_type != "markdown":
            self.notebook.dirty = True
            self.json["cell_type"] = "markdown"
            if "outputs" in self.json:
                del self.json["outputs"]
            if "execution_count" in self.json:
                del self.json["execution_count"]
            self.input_prefix.content = FormattedTextControl(text="")
            self.clear_output()
            self.set_input_readonly()
            if prev_cell_type == "code":
                self.input = HSplit(
                    [ONE_ROW, VSplit([ONE_COL, self.input_window]), ONE_ROW]
                )
                self.notebook.focus(self.notebook.current_cell_idx, update_layout=True)

    def set_as_code(self):
        prev_cell_type = self.json["cell_type"]
        if prev_cell_type != "code":
            self.notebook.dirty = True
            self.json["cell_type"] = "code"
            self.json["outputs"] = []
            self.json["execution_count"] = None
            text = rich_print("\nIn [ ]:" + self.fold_tag(), style="green")
            self.input_prefix.content = FormattedTextControl(text=ANSI(text))
            self.set_input_readonly()
            if prev_cell_type == "markdown":
                self.input = Frame(self.input_window, style="green")
                self.notebook.focus(self.notebook.current_cell_idx, update_layout=True)

    def set_input_readonly(self, mode="interactive"):
        if mode == "batch":
            return
        if self.json["cell_type"] == "markdown":
            text = self.input_buffer.text or "Type *Markdown*"
            md = Markdown(text)
            text = rich_print(md)[:-1]  # remove trailing "\n"
        elif self.json["cell_type"] == "code":
            code = Syntax(
                self.input_buffer.text, self.notebook.language, theme="ansi_dark"
            )
            text = rich_print(code)[:-1]  # remove trailing "\n"
        line_nb = text.count("\n") + 1
        self.input_window.content = FormattedTextControl(text=ANSI(text))
        height_keep = self.input_window.height
        self.input_window.height = line_nb
        if (
            self.notebook.app is not None
            and height_keep is not None
            and line_nb != height_keep
        ):
            # height has changed
            self.notebook.focus(self.notebook.current_cell_idx, update_layout=True)
        if self.fold:
            self.input_window.height = 1

    def open_in_editor(self):
        self.input_buffer.open_in_editor()
        self.notebook.dirty = True
        self.ext_edit = True
        log("-- EDIT IN EDITOR END --")

    def scroll_output(self):
        if self.output.height > 0:
            hshift = self.hshift
            vshift = self.vshift
            self.notebook.dirty = True
            output_ansi, output_height = get_output_text_and_height(
                self.json["outputs"]
            )
            output_text = output_ansi.value
            tmp = output_text.split("\n")
            scrolled = []
            for line in tmp[vshift:]:
                scrolled.append(line[hshift:])
                out_res = "\n".join(scrolled)
            self.output.content = FormattedTextControl(out_res)
            self.output.height = len(scrolled)
            if self.notebook.app:
                self.notebook.focus(self.notebook.current_cell_idx, update_layout=True)

    def scroll_output_up(self):
        if self.output.height > 0 and self.vshift:
            self.vshift -= 1
            self.scroll_output()

    def scroll_output_down(self):
        if self.output.height > 1:
            self.vshift += 1
            self.scroll_output()

    def scroll_output_right(self):
        if self.output.height > 0:
            self.hshift += 1
            self.scroll_output()

    def scroll_output_left(self):
        if self.output.height > 0 and self.hshift > 0:
            self.hshift -= 1
            self.scroll_output()

    def scroll_output_reset(self):
        if self.output.height > 0:
            self.vshift = 0
            self.hshift = 0
            self.scroll_output()

    def open_result_in_editor(self):
        output_ansi, output_height = get_output_text_and_height(self.json["outputs"])
        output_text = output_ansi.value
        self.output_buffer.text = output_text
        self.output_buffer.open_in_editor()

    def set_input_toggle_fold(self):
        execution_count = " "
        if "execution_count" in self.json:
            execution_count = self.json["execution_count"]
        if self.fold is False:
            self.fold = True
            self.input_window.height = 1
            text = rich_print(
                f"\nIn [{execution_count}]:" + self.fold_tag(),
                style="green",
            )
            self.input_prefix.content = FormattedTextControl(text=ANSI(text))
        else:
            self.fold = False
            self.input_window.height = self.input_buffer.text.count("\n") + 1
            text = rich_print(
                f"\nIn [{execution_count}]:" + self.fold_tag(),
                style="green",
            )
            self.input_prefix.content = FormattedTextControl(text=ANSI(text))
        if self.notebook.app:
            self.notebook.app.invalidate()

    def set_input_editable(self):
        if self.json["cell_type"] == "code":
            self.input_window.content = BufferControl(
                buffer=self.input_buffer, lexer=self.notebook.lexer
            )
        else:
            self.input_window.content = BufferControl(buffer=self.input_buffer)
        self.input_window.height = self.input_buffer.text.count("\n") + 1

    def clear_output(self):
        if self.output.height > 0:
            self.notebook.dirty = True
            self.output.height = 0
            self.output.content = FormattedTextControl(text="")
            self.output_prefix.content = FormattedTextControl(text="")
            self.output_prefix.height = 0
            if self.json["cell_type"] == "code":
                self.json["outputs"] = []
            if self.notebook.app:
                self.notebook.focus(self.notebook.current_cell_idx, update_layout=True)

    def update_json(self):
        src_list = [line + "\n" for line in self.input_buffer.text.splitlines()]
        # Fixes exit from cell when nothing is typed neither no output with single cell
        if src_list:
            src_list[-1] = src_list[-1][:-1]
            self.json["source"] = src_list

    def call_external_process(self, fname):
        import subprocess

        try:
            subprocess.call(["python3", fname])
        except subprocess.CalledProcessError as e:
            self.output.content = e.output
            pass
        self.notebook.execution_count += 1
        self.output.content = FormattedTextControl(text="ERROR")

        return self.callback_external_process

    def callback_external_process(self):
        return None

    def run_in_console(self):
        self.clear_output()
        if self.json["cell_type"] == "code":
            code = self.input_buffer.text.strip()
            if code:
                if self not in self.notebook.executing_cells.values():
                    self.notebook.dirty = True
                    executing_text = code
                    fname = "tmp_nbt_"
                    import random

                    for i in range(1, 16):
                        fname += chr(random.randint(97, 122))
                    fname += ".py"
                    f = open(fname, "w")
                    f.write(executing_text)
                    f.close()
                    from prompt_toolkit.application.run_in_terminal import (
                        run_in_terminal,
                    )

                    run_in_terminal(self.call_external_process(fname), in_executor=True)
                    import os

                    os.remove(fname)

    async def run(self):
        try:
            self.clear_output()
            if self.json["cell_type"] == "code":
                code = self.input_buffer.text.strip()
                # log("-- EXEUCING CODE " + str(code))
                if code:
                    if self not in self.notebook.executing_cells.values():
                        self.notebook.dirty = True
                        executing_text = rich_print(
                            "\nIn [*]:" + self.fold_tag(), style="green"
                        )
                        self.input_prefix.content = FormattedTextControl(
                            text=ANSI(executing_text)
                        )
                        self.notebook.execution_count += 1
                        execution_count = self.notebook.execution_count
                        msg_id = uuid.uuid4().hex
                        self.notebook.msg_id_2_execution_count[msg_id] = execution_count
                        self.notebook.executing_cells[execution_count] = self
                        # log execution status
                        self.notebook.kd.log = self.notebook.debug
                        # self.notebook.kd.log = True
                        # test for existence of kernel process sometimes the process
                        # won't start when using the --run parameter so let's be sure
                        # there is one
                        if self.notebook.kd:
                            if not hasattr(self.notebook.kd, "kernel_process"):
                                await self.notebook.kd.start()
                                # print("Starting kd in run")
                        # this is added to eliminate hangs during execution
                        try:
                            await self.notebook.kd.execute(
                                self.input_buffer.text, msg_id=msg_id
                            )
                        except Exception as e:
                            # print("EXCEPTION DURING EXECUTION")
                            self.notebook.kernel_status = "Exception " + str(e)
                            return
                        del self.notebook.executing_cells[execution_count]
                        text = rich_print(
                            f"\nIn [{execution_count}]:" + self.fold_tag(),
                            style="green",
                        )
                        self.input_prefix.content = FormattedTextControl(
                            text=ANSI(text)
                        )
                        self.json["execution_count"] = execution_count
                        if self.notebook.app:
                            self.notebook.app.invalidate()
                else:
                    self.clear_output()
            else:
                self.clear_output()
        except Exception as e:
            print("RUN PROBLEM " + str(e))
