#!/usr/bin/env python3

#
# This file is part of LiteX-Boards.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>,
# Copyright (c) 2020 Staf Verhaegen <staf@fibraservi.eu>
# SPDX-License-Identifier: BSD-2-Clause

import os

from migen import *
from migen.genlib.cdc import MultiReg

from litex.gen import *

from litex_boards.platforms import digilent_arty_s7

from litex.soc.cores.clock import *
from litex.soc.integration.soc import *
from litex.soc.integration.builder import *
from litex.soc.cores.led import LedChaser
from litex.soc.cores.gpio import GPIOIn
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStorage
from litex.build.generic_platform import IOStandard, Pins, Subsignal

from litedram.modules import MT41K128M16
from litedram.phy import s7ddrphy

DAC_SOURCE_DIR = os.path.join(
    os.path.expanduser("~"),
    "audio_elex", "audio_elex", "hw", "deltaSigmaDac", "src"
)
DAC_RTL_FILES = (
    "delta_sigma_dac_stream.v",
    "ds_top.v",
    "audio_linear_interpolator.v",
    "delta_sigma_dac_16bit.v",
    "pdm_to_pwm_transcoder_p_channel_high.v",
)

# CRG ----------------------------------------------------------------------------------------------

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq):
        self.rst          = Signal()
        self.cd_sys       = ClockDomain()
        self.cd_sys2x     = ClockDomain()
        self.cd_sys4x     = ClockDomain()
        self.cd_sys4x_dqs = ClockDomain()
        self.cd_idelay    = ClockDomain()
        self.cd_dac       = ClockDomain()

        # # #

        self.pll = pll = S7PLL(speedgrade=-1)
        self.comb += pll.reset.eq(~platform.request("cpu_reset_n") | self.rst)
        pll.register_clkin(platform.request("clk100"), 100e6)
        pll.create_clkout(self.cd_sys,       sys_clk_freq)
        pll.create_clkout(self.cd_sys2x,     2*sys_clk_freq)
        pll.create_clkout(self.cd_sys4x,     4*sys_clk_freq)
        pll.create_clkout(self.cd_sys4x_dqs, 4*sys_clk_freq, phase=90)
        pll.create_clkout(self.cd_idelay,    200e6)
        pll.create_clkout(self.cd_dac,       50e6)
        platform.add_false_path_constraints(self.cd_sys.clk, pll.clkin) # Ignore sys_clk to pll.clkin path created by SoC's rst.

        self.idelayctrl = S7IDELAYCTRL(self.cd_idelay)

# DAC ----------------------------------------------------------------------------------------------

class Dac(LiteXModule):
    def __init__(self, platform, source_dir):
        source_dir = os.path.abspath(source_dir)
        for filename in DAC_RTL_FILES:
            source = os.path.join(source_dir, filename)
            if not os.path.isfile(source):
                raise FileNotFoundError(f"Missing DAC RTL source: {source}")
            platform.add_source(source, copy=True)

        rom_file = os.path.join(source_dir, "sine_stimulus_unsigned.hex")
        if not os.path.isfile(rom_file):
            raise FileNotFoundError(f"Missing DAC stimulus ROM: {rom_file}")

        self.sink = stream.Endpoint([("data", 16)])
        self.test_mode = CSRStorage(
            1,
            reset=0,
            description="Select the DAC's internal sine test (1) or stream input (0)."
        )

        self.submodules.sample_cdc = stream.ClockDomainCrossing(
            [("data", 16)], cd_from="sys", cd_to="dac", depth=4
        )
        self.comb += self.sink.connect(self.sample_cdc.sink)

        test_mode_dac = Signal()
        self.specials += MultiReg(self.test_mode.storage, test_mode_dac, odomain="dac")

        sample_ready = Signal()
        self.comb += self.sample_cdc.source.ready.eq(sample_ready)

        pads = platform.request("dac")
        self.specials += Instance(
            "delta_sigma_dac_stream",
            p_ROM_FILE=rom_file,
            i_clk=ClockSignal("dac"),
            i_rst_n=~ResetSignal("dac"),
            i_test_mode=test_mode_dac,
            i_sample_data=self.sample_cdc.source.data,
            i_sample_valid=self.sample_cdc.source.valid,
            o_sample_ready=sample_ready,
            o_dac_out=pads.dac_out,
            o_pwm_high=pads.pwm_high,
            o_pwm_low=pads.pwm_low,
            o_pwm_n_high=pads.pwm_n_high,
            o_pwm_n_low=pads.pwm_n_low,
            o_sync_out=pads.sync_out,
        )

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, variant="s7-50", sys_clk_freq=100e6,
        with_spi_flash  = False,
        with_led_chaser = True,
        with_buttons    = False,
        with_switches   = False,
        dac_src         = DAC_SOURCE_DIR,
        **kwargs):
        platform = digilent_arty_s7.Platform(variant=variant)
        platform.add_extension([
            ("dac", 0,
                Subsignal("dac_out",   Pins("pmoda:0")),
                Subsignal("pwm_high",  Pins("pmoda:1")),
                Subsignal("pwm_low",   Pins("pmoda:2")),
                Subsignal("pwm_n_high", Pins("pmoda:3")),
                Subsignal("pwm_n_low",  Pins("pmoda:4")),
                Subsignal("sync_out",  Pins("pmoda:5")),
                IOStandard("LVCMOS33")
            )
        ])

        # CRG --------------------------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq)

        # SoCCore ----------------------------------------------------------------------------------
        SoCCore.__init__(self, platform, sys_clk_freq, ident="LiteX SoC on Arty S7", **kwargs)

        # Delta-Sigma DAC --------------------------------------------------------------------------
        self.dac = Dac(platform, dac_src)

        # DDR3 SDRAM -------------------------------------------------------------------------------
        if not self.integrated_main_ram_size:
            self.ddrphy = s7ddrphy.A7DDRPHY(platform.request("ddram"),
                memtype        = "DDR3",
                nphases        = 4,
                sys_clk_freq   = sys_clk_freq)
            self.add_sdram("sdram",
                phy           = self.ddrphy,
                module        = MT41K128M16(sys_clk_freq, "1:4"),
                l2_cache_size = kwargs.get("l2_size", 8192)
            )

        # SPI Flash --------------------------------------------------------------------------------
        if with_spi_flash:
            from litespi.modules import S25FL128S
            from litespi.opcodes import SpiNorFlashOpCodes as Codes
            self.add_spi_flash(mode="4x", module=S25FL128S(Codes.READ_1_1_4), with_master=True)

        # Leds -------------------------------------------------------------------------------------
        if with_led_chaser:
            self.leds = LedChaser(
                pads         = platform.request_all("user_led"),
                sys_clk_freq = sys_clk_freq)

        # Buttons / Switches -----------------------------------------------------------------------
        if with_buttons:
            self.buttons = GPIOIn(Cat(platform.request_all("user_btn")))
        if with_switches:
            self.switches = GPIOIn(Cat(platform.request_all("user_sw")))

# Build --------------------------------------------------------------------------------------------

def main():
    from litex.build.parser import LiteXArgumentParser
    parser = LiteXArgumentParser(platform=digilent_arty_s7.Platform, description="LiteX SoC on Arty S7.")
    parser.add_target_argument("--variant",        default="s7-50",           help="Board variant (s7-50 or s7-25).")
    parser.add_target_argument("--sys-clk-freq",   default=100e6, type=float, help="System clock frequency.")
    parser.add_target_argument("--with-spi-flash", action="store_true",       help="Enable memory-mapped SPI flash.")
    parser.add_target_argument("--with-buttons",   action="store_true",       help="Enable Buttons.")
    parser.add_target_argument("--with-switches",  action="store_true",       help="Enable Switches.")
    parser.add_target_argument(
        "--dac-src",
        default=DAC_SOURCE_DIR,
        help="Directory containing the delta-sigma DAC RTL and stimulus ROM."
    )
    args = parser.parse_args()

    soc = BaseSoC(
        variant        = args.variant,
        sys_clk_freq   = args.sys_clk_freq,
        with_spi_flash = args.with_spi_flash,
        with_buttons   = args.with_buttons,
        with_switches  = args.with_switches,
        dac_src        = args.dac_src,
        **parser.soc_argdict
    )
    builder = Builder(soc, **parser.builder_argdict)
    if args.build:
        builder.build(**parser.toolchain_argdict)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))

if __name__ == "__main__":
    main()
