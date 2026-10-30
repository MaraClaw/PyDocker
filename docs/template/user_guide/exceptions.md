# Capturing exceptions

## Exception classes

Exceptions raised will be an instance of `DockerException`, or a child class for
more specific errors.

Those are the child classes:

* `NoSuchContainer`
* `NoSuchImage`
* `NoSuchService`
* `NotASwarmManager`
* `NoSuchVolume`
* `NoSuchConfig`
* `NoSuchSecret`
* `NoSuchPlugin`
* `NoSuchContext`

A class is chosen from the text docker wrote to stderr. A line has to begin with the
resource kind's own phrase, such as `No such secret: x` or `config x not found`,
optionally after `Error response from daemon: ` or `Error: `. The name in the
message and file paths never decide the class, so a secret called `network-x` is
still a `NoSuchSecret`. Output of your own application that `run` or `exec`
forwards on stderr can begin a line with one of those phrases and be misclassified;
PyWhales does nothing to prevent that.

Config, secret, plugin, context, and service handles have an `exists()` method that
returns `False` only for their own `NoSuch...` class. Other failures, including
`NotASwarmManager`, propagate. A config, secret, plugin, or service that was removed
inside its `with` block is not an error when the block exits.

All exceptions will have these 4 attributes:

* docker_command: the docker command used internally, as a list of strings.
* return_code: the exit code docker client exited with, as an int
* stdout: the content that docker wrote to stdout, as a string, or `None`
* stderr: the content that docker wrote to stderr, as a string, or `None`

Automatic failure messages, debug printing, and logger records withhold command,
environment, and captured-output values by default. Exception classes and exit
codes remain available. The four attributes above still contain raw values;
avoid logging them where they could expose credentials or application data.

Use `DockerClient(raw_diagnostics=True)` on a separate troubleshooting client to
enable detailed diagnostics. This also permits raw inspect-response dumps when
model parsing fails. Returned payloads, uncaptured terminal output, and values
you log yourself are not protected by this policy.

## Example

```python
import logging
from pywhales import DockerClient
from pywhales.exceptions import DockerException

client = DockerClient(compose_files=["/tmp/docker-compose.yml"])
try:
    client.execute("my-service", ["arg1", "arg2"])
except DockerException as e:
    print(f"Docker command failed with exit code {e.return_code}")
```
