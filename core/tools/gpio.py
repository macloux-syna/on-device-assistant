# https://synaptics-astra.github.io/doc/v/scarthgap_6.12_v2.0.0/subject/gpios.html

from pathlib import Path
import time

import gpiod
from gpiod.line import Direction, Value


class GPIOPinInfo:

    def __init__(self, gpio_pin: int):
        if not isinstance(gpio_pin, int):
            raise TypeError(f"GPIO pin number must be an integer, not {type(gpio_pin)}")
        if not (0 <= gpio_pin <= 95):
            raise ValueError(f"Invalid GPIO pin number '{gpio_pin}' (expected 0 - 95)")

        self._gpio_pin = gpio_pin
        self._gpio_line: int | None = None
        self._gpio_addr: str | None = None
        self._gpio_chip: str | None = None

        self.get_gpio_info()

    @property
    def chip(self) -> str:
        if self._gpio_chip is None:
            self.get_gpio_info()
        return self._gpio_chip

    @property
    def line(self) -> int:
        if self._gpio_line is None:
            self.get_gpio_info()
        return self._gpio_line

    def _detect_soc(self) -> str | None:
        # Prefer a more robust source if available; fall back to hostname
        try:
            hostname = Path("/etc/hostname").read_text().strip()
        except OSError:
            print("WARNING: Failed to read /etc/hostname")
            return None
        return hostname

    def _get_chip_addr(self, group: int) -> str | None:
        assert 0 <= group < 3, f"Invalid GPIO group {group}"

        addresses = {
            # 1620 mappings from:
            # https://synaptics-astra.github.io/doc/v/scarthgap_6.12_v2.0.0/subject/gpios.html#sl1620
            "sl1620": {0: "f7e80800", 1: "f7e80c00", 2: "f7e81000"},

            # 1640 and 1680 (identical) mappings from:
            # https://synaptics-astra.github.io/doc/v/scarthgap_6.12_v2.0.0/subject/gpios.html#sl1640-sl1680
            "sl1640": {0: "f7e82400", 1: "f7e80800", 2: "f7e80c00"},
            "sl1680": {0: "f7e82400", 1: "f7e80800", 2: "f7e80c00"},
        }

        soc = self._detect_soc()
        if soc is None:
            return None

        if "sl1620" in soc:
            family = "sl1620"
        elif "sl1640" in soc:
            family = "sl1640"
        elif "sl1680" in soc:
            family = "sl1680"
        else:
            print(f"WARNING: Unknown SoC/hostname '{soc}'")
            return None

        return addresses[family][group]

    def get_address_info(self) -> tuple[str, int]:
        gpio_pin = self._gpio_pin

        if 0 <= gpio_pin < 32:
            group, offset = 0, 0
        elif 32 <= gpio_pin < 64:
            group, offset = 1, 32
        elif 64 <= gpio_pin < 96:
            group, offset = 2, 64
        else:
            raise ValueError(f"GPIO pin {gpio_pin} out of supported range")

        chip_addr = self._get_chip_addr(group)
        if chip_addr is None:
            raise RuntimeError(f"Unable to resolve chip address for GPIO pin {gpio_pin}")

        self._gpio_addr = chip_addr
        self._gpio_line = gpio_pin - offset
        return self._gpio_addr, self._gpio_line

    def get_gpio_chip(self) -> str:
        if self._gpio_addr is None:
            self.get_address_info()

        gpio_chips = list(Path("/dev").glob("gpiochip*"))
        if not gpio_chips:
            raise RuntimeError("No /dev/gpiochip* devices found")

        for chip_path in gpio_chips:
            chip_str = str(chip_path)
            with gpiod.Chip(chip_str) as chip:
                info = chip.get_info()
                if self._gpio_addr in info.label:
                    self._gpio_chip = chip_str
                    return self._gpio_chip

        raise ValueError(f"GPIO chip for address '{self._gpio_addr}' not found")

    def get_gpio_info(self) -> tuple[str, int]:
        addr, line = self.get_address_info()
        chip = self.get_gpio_chip()
        return chip, line


class GPIO:

    def __init__(self, chip: str, line: int):
        self.chip = chip
        self.line = line

    @classmethod
    def from_gpio_pin(cls, gpio_pin: int) -> "GPIO":
        pin_info = GPIOPinInfo(gpio_pin)
        return cls(pin_info.chip, pin_info.line)

    def read(self) -> Value:
        settings = gpiod.LineSettings(direction=Direction.INPUT)
        with gpiod.request_lines(self.chip, config={self.line: settings}) as req:
            value = req.get_value(self.line)
            return value

    def write(self, value: Value):
        settings = gpiod.LineSettings(direction=Direction.OUTPUT)
        with gpiod.request_lines(self.chip, config={self.line: settings}) as req:
            req.set_value(self.line, value)

    def set_high(self):
        self.write(Value.ACTIVE)

    def set_low(self):
        self.write(Value.INACTIVE)

    def pulse_high(self, duration_sec: float):
        with gpiod.request_lines(self.chip, config={self.line: gpiod.LineSettings(direction=Direction.OUTPUT)}) as req:
            req.set_value(self.line, Value.ACTIVE)
            time.sleep(duration_sec)
            req.set_value(self.line, Value.INACTIVE)

    def wait_for_value(self, target_value: Value, timeout: float) -> int | None:
        deadline = time.monotonic_ns() + int(timeout * 1e9)
        with gpiod.request_lines(self.chip, config={self.line: gpiod.LineSettings(direction=Direction.INPUT)}) as req:
            while time.monotonic_ns() < deadline:
                if req.get_value(self.line) == target_value:
                    return time.monotonic_ns()
                time.sleep(0.001)
        return None


def main():
    gpio = GPIO.from_gpio_pin(args.gpio_pin)
    if args.read:
        val = gpio.read()
        print(1 if val == Value.ACTIVE else 0)
    else:
        if args.write == 1:
            gpio.set_high()
        else:
            gpio.set_low()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Control GPIO pin")
    parser.add_argument(
        "gpio_pin",
        type=int,
        help="GPIO pin number"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "-r", "--read",
        action="store_true",
        default=False,
        help="Read GPIO value"
    )
    action.add_argument(
        "-w", "--write",
        type=int,
        metavar="{0 | 1}",
        choices=[0, 1],
        help="Write value to GPIO (0 or 1)"
    )
    args = parser.parse_args()

    main()