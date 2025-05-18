# TDSR

This is a fork of the popular console-based screen reader called tdsr.
It has been tested under macOS, Linux and FreeBSD.
It might also run on other \*nix systems, but this hasn't been tested.
Compatibility is not guaranteed between versions.

### What works

* Reading output
* Reading by line, word and character
* cursor keys (waits some amount of time and speaks)

### Changes since fork

This is a fork and there have been some minor changes:

* now installable via `pip` or `pipx`
  * you no longer have to clone the repo, but you can if you want
* config file now lives in `~/.config/tdsr/tdsr.cfg` (but we still respect the old location)
* major linting changes to use spaces instead of tabs
* adds a `TDSR_ACTIVE=true` env var setting

## Requirements

* Python 3
* speech server

### Requirements for development

* [poetry](https://python-poetry.org/docs/#installation), which can be installed with `pipx install poetry`

## Installation

There are two different packages, one for linux called `tdsr2` and one for macos called `tdsr-mac`. This is because different operating systems require different base packages that can only be built and installed on their base operating system.

### macOS

1. Install Python 3. If using [Homebrew](http://brew.sh/), `brew install python3`.
2. Install [pipx](https://pipx.pypa.io/stable/installation/#installing-pipx). (we recommend pipx because it uses a python environment to install the code, so that you don't upset the balance of your system level pythong packages.) If using homebrew, you can install it with `brew install pipx`.
3. `pip install tdsr-mac`

### Linux

1. Install Python 3 and Speech Dispatcher.  They should be available from your package manager. (on debian/ubuntu you can do `sudo apt install speech-dispatcher`) You may also need to install Speech Dispatcher's Python bindings, if they were packaged separately by your distro.
2. Install [pipx](https://pipx.pypa.io/stable/installation/#installing-pipx). (we recommend pipx because it uses a python environment to install the code, so that you don't upset the balance of your system level pythong packages.) If using homebrew, you can install it with `brew install pipx`, and if using apt you should be able to do `apt install pipx`.
3. `pipx install tdsr2`


### Terminal setup

Open Terminal preferences, under Profiles check Use Option as Meta key.

## Keys

(alt refers to the meta key.)
* alt u, i, o - read previous, current, next line
* alt j, k, l - read previous, current, next word
* alt m, comma, dot - read previous, current, next character
* alt k twice - spell current word
* alt comma twice - say current character phonetically
* alt c - config.
* alt q - quiet mode on/off. When on, text is not automatically read.
* alt r - start/end selection.
* alt v - copy mode. Press l to copy the line the review cursor is on, or s to copy the screen.

## Configuration

Once in the config menu, you can use:
* r - set rate.
* v - set volume (value between 0 and 100).
* p - toggle symbol processing.
* d - set cursor delay (in MS). The default is 20.
* l - Toggle pausing at newlines.
* s - Toggle repeated symbols
* Enter - exit, saving the configuration.

## Symbols

Symbols can be added in the configuration file (`~/.config/tdsr/tdsr.cfg`),
under the symbols section.

The format is:
```
character code = name
```
Because of how the config system works, it's best to do this with one TDSR open, then exit and re-launch to see the changes.

## Plugins

Custom key binds and handlers can be added via the plugins and commands section of the config files
and a python module in the plugins directory that exports the following method signature:

```python
# Name: parse_output
# Parameters: an array of strings (the lines from the terminal)
# Returns: an array of strings (the things to speak)
def parse_output(lines):
    return ["a list of things to say"]
```

### Config file

The config file lives in `~/.config/tdsr/tdsr.cfg`, but if you still have a file at `~/.tdsr.cfg`, we will respect it and ignore the previous path. In the config file you add to the plugins and commands section to modify the shortcut and terminal command that has been run.

Required: [plugins] The plugin section maps to a letter you press with alt to trigger the plugin.

Optional: [commands] The command section is a regex of the command you ran previous to triggering the plugin (this minimizes processing time)

Optional: A regex to indicate the start of your prompt line in your terminal

#### Example

To add a shortcut for alt d to trigger a plugin called my_plugin add the following under [plugins]

```
my_plugin = d
```
If you have sub folders, separate them with a dot e.g. for plugins/me/my_plugin

```
me.my_plugin = d
```

To specify a command of `echo "hi"` (which makes parsing slightly more efficient) add the following under [commands]
```
my_plugin = echo "hi"
```
Use dots for sub folders, like the plugin config. You can use a regular expression to make it more flexible, e.g. to
specify a command of `echo "hi"` or `echo "bye"`

```
my_plugin = echo "(hi|bye)"
```

The default prompt is match anything, if you use zsh you can use the following regular expression under [speech]:

```
prompt = ^➜\s{2}.+✗?
```

#### Errors

If you hear "error loading plugin" followed by an error, you can launch tdsr in debug mode

```commandline
~/tdsr --debug
```

And search the logs for "Error loading plugin" to see more details

## Repeating symbols

Symbols you would like condensed down to "42 =" instead of "= = = =" you can specify under the speech section

```
repeated_symbols_values = -_=!
```

# Development

If you would like to develop locally, follow these steps to test things:

1. clone the repo: `git clone https://github.com/jessebot/tdsr.git`
2. go into repo directory: `cd tdsr`
3. install the local virtual env: `poetry install`
4. get into a poetry environment. (I use poetry shell to get into the virtual env. this requires you to run `pipx inject poetry poetry-plugin-shell` to install the shell plugin for poetry.) If using poetry shell plugin, run `poetry shell`.

From there you can run `tdsr` and it will pull your live developed version of the code as you change things. Please remember to bump the version in pyproject.toml if contributing back to this codebase, and then a github workflow will run when your pull request is merged to main to automatically release the new version that people can install with pipx.

## Publishing

If you'd like to publish your own version of tdsr, be sure to change the name variable in pyproject.toml. We have a small script for publishing for mac specifically in [release_mac.sh](./release_mac.sh). Please change the name `tdsr-mac` to another name before using it.

If you want to publish to pypi, go to pypi.org, login to your account, and at the top right corner select your account dropdown menu. Select "Account settings", and then towards the bottom of the page, select the "Add API token" button. Fill in your token name and set the scope for your entire account, so you can use this for other projects too. It will generate a token that you need to save somewhere safe. After that, you can run the `poetry config` command below, but replace pypi-your-full-token-here with your full token.

```bash
poetry config pypi-token.pypi pypi-your-full-token-here
```

After you've configured this, you can run `poetry lock` to generate a lock file of the specific versions of your dependencies, and then `poetry publish --build`, so build and publish your package. If you have further questions, please feel free to ping me on fedi at https://social.smallhack.org/@jessebot and I can answer any questions you have.

## License

Copyright (C) 2016, 2017  Tyler Spivey

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.

Changes after 2024 are from @stormdragon2976 and @jessebot.
