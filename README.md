# LLM controlled drone SDK
## Setup
### Prereqs
Docker Desktop, Rust, uv, Python 3.12

### Step 1: Setup project structure
create a repo to work in 
```bash
mkdir ~/ws
```
Clone bv_bevy
```bash
cd ~/ws
git clone https://github.com/BuckeyeVertical/bv_bevy.git
```
Clone this repo (bv_llm_sdk)
```bash
cd ~/ws
git clone https://github.com/BuckeyeVertical/bv_llm_sdk.git
```
Should be in this layout:
```text
ws/
├── bv_bevy/
└── bv_llm_sdk/
```

### Step 2: Build dev environment
Install python venv deps
```bash
cd ~/ws/bv_llm_sdk
uv sync
```
Note: this project uses uv as a package manager so if your adding deps use ```uv add ...```

Build Gazebo and PX4 image
```bash
cd ~/ws/bv_bevy
docker compose -f gazebo/compose.px4.yaml build
cargo build
```

## Run simulation

Start Gazebo and PX4 first:
```bash
cd ~/ws/bv_bevy
docker compose -f gazebo/compose.px4.yaml up
```

Start Bevy:
```bash
cd ~/ws/bv_bevy
./run_suas.sh
```



