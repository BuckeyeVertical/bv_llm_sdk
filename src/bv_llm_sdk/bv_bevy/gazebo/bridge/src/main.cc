#include <atomic>
#include <chrono>
#include <csignal>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <utility>

#include "bv_sim_bridge/bridge_config.hh"
#include "bv_sim_bridge/gazebo_state_source.hh"
#include "bv_sim_bridge/tcp_state_server.hh"

namespace
{

std::atomic_bool running{true};

void Stop(int)
{
  running.store(false);
}

}

int main(int argc, char **argv)
{
  if (argc != 2)
  {
    std::cerr << "Usage: bv_sim_bridge CONFIG.json" << std::endl;
    return 2;
  }

  std::signal(SIGINT, Stop);
  std::signal(SIGTERM, Stop);

  try
  {
    auto config = bv::sim::BridgeConfig::Load(argv[1]);
    bv::sim::TcpStateServer server(config.listenAddress, config.listenPort);
    bv::sim::GazeboStateSource source(
      config,
      [&server](std::string snapshot) {
        server.Publish(std::move(snapshot));
      });

    if (!source.Start())
    {
      throw std::runtime_error("cannot subscribe to " + config.poseTopic);
    }

    std::cout << "Subscribed to " << config.poseTopic << std::endl;

    while (running.load())
    {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
  }
  catch (const std::exception &error)
  {
    std::cerr << "Bridge failed: " << error.what() << std::endl;
    return 1;
  }

  return 0;
}
