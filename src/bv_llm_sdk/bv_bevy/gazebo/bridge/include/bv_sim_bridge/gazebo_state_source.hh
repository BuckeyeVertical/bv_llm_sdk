#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <unordered_map>

#include <gz/msgs/pose_v.pb.h>
#include <gz/transport/Node.hh>

#include "bv_sim_bridge/bridge_config.hh"

namespace bv::sim
{

class GazeboStateSource
{
public:
  using SnapshotHandler = std::function<void(std::string)>;

  GazeboStateSource(BridgeConfig config, SnapshotHandler handler);

  bool Start();

private:
  void OnPose(const gz::msgs::Pose_V &message);

  BridgeConfig config_;
  SnapshotHandler handler_;
  gz::transport::Node node_;
  std::unordered_map<std::string, EntityMapping> mappings_;
  std::string streamId_;
  std::uint64_t sequence_{0};
};

}
