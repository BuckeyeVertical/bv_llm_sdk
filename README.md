# BV LLM SDK

Control a robot by typing plain English. You ask for something like "draw a square",
and an AI model decides which robot commands to run.

There are two robots to choose from, called toolkits: a drone in a simulator and a
robot arm that draws on paper. The setup below is the same for both.

## Prerequisites

Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Setup

If you just installed Git or uv, close the terminal and open a new one first.

### 1. Make a folder to work in

```bash
mkdir ~/ws
```

### 2. Download the project

```bash
cd ~/ws
```

```bash
git clone -b construct-io-starter https://github.com/BuckeyeVertical/bv_llm_sdk.git
```

### 3. Go into the project folder

```bash
cd ~/ws/bv_llm_sdk
```

Run every command from here on inside this folder.

### 4. Add the API key

Make your own settings file by copying the example:

```bash
cp .env.example .env
```

Open `.env` in a text editor.

Replace `your-vllm-api-key` with the key from the organizer, so the file looks like this
with your key after the `=`:

```text
BV_AGENT_API_KEY=the-key-goes-here
```

### 5. Install Python and the packages

```bash
uv sync --extra agent
```

### 6. Check that everything works

```bash
uv run scripts/check_setup.py
```

You should see this:

```text
Setup is working. Connected to Qwen/Qwen3.6-35B-A3B-FP8.
```

## Drone setup

Follow this section if you are using the drone. It runs in a simulator on your computer.

### Prerequisites


| Program | What it is for | Check it worked |
| --- | --- | --- |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | Runs the flight simulator | `docker --version` |
| [Rust](https://rustup.rs) | Builds the 3D world | `cargo --version` |

If `cargo` isn't found after installing Rust, close the terminal and open a new one.

### 1. Download the simulator

```bash
cd ~/ws
```

```bash
git clone https://github.com/BuckeyeVertical/bv_bevy.git
```

### 2. Start the flight simulator

In your first terminal, run:

```bash
cd ~/ws/bv_bevy
```

```bash
./px4.sh --prebuilt forest    # Mac/Linux
.\px4.ps1 --prebuilt forest   # Windows
```

This downloads the simulator, which can take a while. It then waits for the build in
step 3, so it may look stuck. Leave it running.

If Windows PowerShell won't run `px4.ps1`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### 3. Build the 3D world

Open a second terminal and run:

```bash
cd ~/ws/bv_bevy
```

```bash
cargo build --release
```

The first build can take a long time.

### 4. Open the 3D world

When the build finishes, run this in the second terminal:

```bash
cargo run --release -- forest
```

A window opens showing the forest and the drone. Leave both terminals running.

### 5. Install the camera packages

Open a third terminal and run:

```bash
cd ~/ws/bv_llm_sdk
```

```bash
uv sync --extra agent --extra vision
```

This is a large download. Then download the object detection model:

```bash
uv run python -c "from bv_llm_sdk.toolkits.drone.vision import get_detector; get_detector()"
```

### 6. Check that the drone is connected

```bash
uv run scripts/tool.py get_status
```

You should see the drone's status printed with no errors.

### 7. Chat with the drone

```bash
uv run scripts/chat.py --toolkit drone
```

When you see `you>`, type a request and press Enter:

```text
you> take off to 10 meters and fly a square
```

If you see `write the agent loop`, the chat is working. Your next job is in
[your_task.md](your_task.md).

Type `quit` to exit the chat. To stop the simulator, press Ctrl+C in the first and second
terminals.

## Robot arm setup

Follow this section if you are using the robot arm.

### 1. Install the arm packages

```bash
cd ~/ws/bv_llm_sdk
```

```bash
uv sync --extra agent --extra arm
```

If you are also using the drone, run this instead so you keep the camera packages:

```bash
uv sync --extra agent --extra vision --extra arm
```

### 2. Connect the arm

Plug the arm into your computer with the USB cable, then turn on the servo power supply.

### 3. Check that the arm is connected

Hold the arm before you run this command, because it may go limp when the program connects.

```bash
uv run scripts/chat.py --toolkit arm
```

When it asks, pose the arm in its park pose by hand. An organizer can show you this pose.
Keep your hands clear and press Enter, and the arm will hold itself up.

When you see `you>`, the arm is connected. Type `quit` to exit.


### No arm?

Open `.env` in a text editor and add this line to use a fake arm. It checks each move but
nothing physical happens.

```text
BV_ARM_SIM=1
```

## Your task

When your robot is set up, go to [your_task.md](your_task.md).
