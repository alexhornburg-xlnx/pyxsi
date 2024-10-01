# Copyright (C) 2024, Advanced Micro Devices, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of pyxsi nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import errno
import os
import os.path
import pyxsi
import subprocess
import sys


def launch_process_helper(args, proc_env=None, cwd=None):
    """Helper function to launch a process in a way that facilitates logging
    stdout/stderr with Python loggers.
    Returns (cmd_out, cmd_err)."""
    if proc_env is None:
        proc_env = os.environ.copy()
    with subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=proc_env, cwd=cwd
    ) as proc:
        (cmd_out, cmd_err) = proc.communicate()
    if cmd_out is not None:
        cmd_out = cmd_out.decode("utf-8")
        sys.stdout.write(cmd_out)
    if cmd_err is not None:
        cmd_err = cmd_err.decode("utf-8")
        sys.stderr.write(cmd_err)
    return (cmd_out, cmd_err)

def compile_sim_obj(top_module_name, source_list, sim_out_dir):
    # create a .prj file with the source files
    with open(sim_out_dir + "/rtlsim.prj", "w") as f:
        for src_line in source_list:
            if src_line.endswith(".v"):
                f.write(f"verilog work {src_line}\n")
            elif src_line.endswith(".vhd"):
                f.write(f"vhdl2008 work {src_line}\n")
            else:
                raise Exception(f"Unknown extension for .prj file sources: {src_line}")

    # now call xelab to generate the .so for the design to be simulated
    # TODO make debug controllable to allow faster sim when desired
    # list of libs for xelab retrieved from Vitis HLS cosim cmdline
    xelab_libs = [
        "smartconnect_v1_0", "axi_protocol_checker_v1_1_12", "axi_protocol_checker_v1_1_13", 
        "axis_protocol_checker_v1_1_11", "axis_protocol_checker_v1_1_12", "xil_defaultlib", 
        "unisims_ver", "xpm", "floating_point_v7_1_16", "floating_point_v7_0_21"
    ]

    cmd_xelab = [
        "xelab",
        "work." + top_module_name,
        "-relax",
        "-prj",
        "rtlsim.prj",
        "-debug",
        "all",
        "-dll",
        "-s",
        top_module_name,
    ]
    for lib in xelab_libs:
        cmd_xelab.append("-L")
        cmd_xelab.append(lib)

    launch_process_helper(cmd_xelab, cwd=sim_out_dir)
    out_so_relative_path = "xsim.dir/%s/xsimk.so" % top_module_name
    out_so_full_path = sim_out_dir + "/" + out_so_relative_path

    if not os.path.isfile(out_so_full_path):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), out_so_full_path)

    return (sim_out_dir, out_so_relative_path)


def load_sim_obj(sim_out_dir, out_so_relative_path, tracefile=None, is_toplevel_verilog=True):
    oldcwd = os.getcwd()
    os.chdir(sim_out_dir)
    sim = pyxsi.XSI(
        out_so_relative_path,
        is_toplevel_verilog=is_toplevel_verilog,
        tracefile=tracefile,
        logfile="rtlsim.log",
    )
    os.chdir(oldcwd)
    return sim


def _find_signal(sim, signal_name):
    signal_list = [sim.get_port_name(i) for i in range(sim.get_port_count())]
    # handle both mixed caps and lowercase signal names
    if signal_name in signal_list:
        return signal_name
    elif signal_name.lower() in signal_list:
        return signal_name.lower()
    else:
        raise Exception("Signal not found: " + signal_name)


def _read_signal(sim, signal_name):
    signal_name = _find_signal(sim, signal_name)
    port_val = sim.get_port_value(signal_name)
    return int(port_val, 2)


def _write_signal(sim, signal_name, signal_value):
    signal_name = _find_signal(sim, signal_name)
    signal_len = len(sim.get_port_value(signal_name))
    if signal_value < 0:
        raise Exception("TODO: _write_signal needs fix for 2s complement neg values")
    signal_bin_value = f"{signal_value:0{signal_len}b}"[-signal_len:]
    sim.set_port_value(signal_name, signal_bin_value)


def reset_rtlsim(sim, rst_name="ap_rst_n", active_low=True, clk_name="ap_clk"):
    _write_signal(sim, clk_name, 0)
    _write_signal(sim, rst_name, 0 if active_low else 1)
    for _ in range(2):
        toggle_clk(sim, clk_name)

    signals_to_write = {}
    signals_to_write[rst_name] = 1 if active_low else 0
    toggle_clk(sim, clk_name, signals_to_write)
    toggle_clk(sim, clk_name)


def toggle_clk(sim, clk_name="ap_clk", signals_to_write={}):
    toggle_neg_edge(sim, clk_name=clk_name)
    toggle_pos_edge(sim, clk_name=clk_name, signals_to_write=signals_to_write)


def toggle_neg_edge(sim, clk_name="ap_clk"):
    _write_signal(sim, clk_name, 0)
    sim.run(5000)


def toggle_pos_edge(sim, clk_name="ap_clk", signals_to_write={}):
    _write_signal(sim, clk_name, 1)
    sim.run(5000)
    # Write IO signals a delta cycle after rising edge
    if bool(signals_to_write):  # if dict non-empty
        for sig in signals_to_write.keys():
            _write_signal(sim, sig, signals_to_write[sig])
    comb_update_and_trace(sim)


def comb_update_and_trace(sim):
    # TODO anything needed here for tracing or updates?
    pass


def rtlsim_multi_io(
    sim,
    io_dict,
    num_out_values,
    sname="_V_V_",
    liveness_threshold=10000,
    hook_preclk=None,
    hook_postclk=None,
):
    """Runs the XSI-based simulation by passing the input values to the simulation,
    toggle the clock and observing the execution time. Function contains also an
    observation loop that can abort the simulation if no output value is produced
    after a set number of cycles. Can handle multiple i/o streams. See function
    implementation for details on how the top-level signals should be named.

    Arguments:

    * sim: the pyxsi object for simulation
    * io_dict: a dict of dicts in the following format:
      {"inputs" : {"in0" : <input_data>, "in1" : <input_data>},
      "outputs" : {"out0" : [], "out1" : []} }
      <input_data> is a list of Python arbitrary-precision ints indicating
      what data to push into the simulation, and the output lists are
      similarly filled when the simulation is complete
    * num_out_values: number of total values to be read from the simulation to
      finish the simulation and return.
    * sname: signal naming for streams, "_V_V_" by default, vitis_hls uses "_V_"
    * liveness_threshold: if no new output is detected after this many cycles,
      terminate simulation
    * hook_preclk: hook function to call prior to clock tick
    * hook_postclk: hook function to call after clock tick

    Returns: number of clock cycles elapsed for completion

    """

    for outp in io_dict["outputs"]:
        _write_signal(sim, outp + sname + "TREADY", 1)

    # observe if output is completely calculated
    # total_cycle_count will contain the number of cycles the calculation ran
    output_done = False
    total_cycle_count = 0
    output_count = 0
    old_output_count = 0

    # avoid infinite looping of simulation by aborting when there is no change in
    # output values after 100 cycles
    no_change_count = 0

    # Dictionary that will hold the signals to drive to DUT
    signals_to_write = {}

    while not (output_done):
        if hook_preclk:
            hook_preclk(sim)
        # Toggle falling edge to arrive at a delta cycle before the rising edge
        toggle_neg_edge(sim)

        # examine signals, decide how to act based on that but don't update yet
        # so only _read_signal access in this block, no _write_signal
        for inp in io_dict["inputs"]:
            inputs = io_dict["inputs"][inp]
            signal_name = inp + sname
            if (
                _read_signal(sim, signal_name + "TREADY") == 1
                and _read_signal(sim, signal_name + "TVALID") == 1
            ):
                inputs = inputs[1:]
            io_dict["inputs"][inp] = inputs

        for outp in io_dict["outputs"]:
            outputs = io_dict["outputs"][outp]
            signal_name = outp + sname
            if (
                _read_signal(sim, signal_name + "TREADY") == 1
                and _read_signal(sim, signal_name + "TVALID") == 1
            ):
                outputs = outputs + [_read_signal(sim, signal_name + "TDATA")]
                output_count += 1
            io_dict["outputs"][outp] = outputs

        # update signals based on decisions in previous block, but don't examine anything
        # so only _write_signal access in this block, no _read_signal
        for inp in io_dict["inputs"]:
            inputs = io_dict["inputs"][inp]
            signal_name = inp + sname
            signals_to_write[signal_name + "TVALID"] = 1 if len(inputs) > 0 else 0
            signals_to_write[signal_name + "TDATA"] = inputs[0] if len(inputs) > 0 else 0

        # Toggle rising edge to arrive at a delta cycle before the falling edge
        toggle_pos_edge(sim, signals_to_write=signals_to_write)
        if hook_postclk:
            hook_postclk(sim)

        total_cycle_count = total_cycle_count + 1

        if output_count == old_output_count:
            no_change_count = no_change_count + 1
        else:
            no_change_count = 0
            old_output_count = output_count

        # check if all expected output words received
        if output_count == num_out_values:
            output_done = True

        # end sim on timeout
        if no_change_count == liveness_threshold:
            raise Exception(
                "Error in simulation! Takes too long to produce output. "
                "Consider setting the liveness_threshold parameter to a "
                "larger value."
            )

    return total_cycle_count
