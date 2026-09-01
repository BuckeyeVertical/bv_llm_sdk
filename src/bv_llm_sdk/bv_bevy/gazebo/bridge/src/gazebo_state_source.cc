#include "bv_sim_bridge/gazebo_state_source.hh"

#include <chrono>
#include <iomanip>
#include <random>
#include <sstream>
#include <utility>

#include <nlohmann/json.hpp>

#include "bv_sim_bridge/pose_graph.hh"

namespace bv::sim
{
namespace
{

std::string MakeStreamId()
{
  std::random_device random;
  std::uniform_int_distribution<std::uint32_t> distribution;
  std::ostringstream output;
  output << std::hex << std::setfill('0');

  for (int index = 0; index < 4; ++index)
  {
    output << std::setw(8) << distribution(random);
  }

  return output.str();
}

std::int64_t StampNanoseconds(const gz::msgs::Pose_V &message)
{
  const gz::msgs::Header *header = nullptr;

  if (message.has_header() && message.header().has_stamp())
  {
    header = &message.header();
  }
  else if (message.pose_size() > 0 && message.pose(0).has_header() &&
           message.pose(0).header().has_stamp())
  {
    header = &message.pose(0).header();
  }

  if (header == nullptr)
  {
    return -1;
  }

  return static_cast<std::int64_t>(header->stamp().sec()) * 1'000'000'000LL +
         header->stamp().nsec();
}

nlohmann::json SerializeEntity(
  const gz::math::Pose3d &pose,
  const EntityMapping &mapping)
{
  return {
    {"id", mapping.id},
    {"kind", mapping.kind},
    {"position_m", {
      pose.Pos().X(),
      pose.Pos().Y(),
      pose.Pos().Z(),
    }},
    {"orientation_xyzw", {
      pose.Rot().X(),
      pose.Rot().Y(),
      pose.Rot().Z(),
      pose.Rot().W(),
    }},
  };
}

}

GazeboStateSource::GazeboStateSource(
  BridgeConfig config,
  SnapshotHandler handler)
  : config_(std::move(config)),
    handler_(std::move(handler)),
    streamId_(MakeStreamId())
{
  for (const auto &mapping : config_.entities)
  {
    mappings_.emplace(mapping.gazeboName, mapping);
  }
}

bool GazeboStateSource::Start()
{
  return node_.Subscribe(config_.poseTopic, &GazeboStateSource::OnPose, this);
}

void GazeboStateSource::OnPose(const gz::msgs::Pose_V &message)
{
  const auto simTimeNs = StampNanoseconds(message);
  if (simTimeNs < 0)
  {
    return;
  }

  auto entities = nlohmann::json::array();
  const auto worldPoses = ResolveWorldPoses(message);

  for (const auto &[gazeboName, mapping] : mappings_)
  {
    const auto pose = worldPoses.find(gazeboName);
    if (pose != worldPoses.end())
    {
      entities.push_back(SerializeEntity(pose->second, mapping));
    }
  }

  const nlohmann::json snapshot{
    {"schema", "bv.sim_state"},
    {"version", 1},
    {"stream_id", streamId_},
    {"sequence", sequence_++},
    {"sim_time_ns", simTimeNs},
    {"frame_id", "gazebo_world"},
    {"entities", std::move(entities)},
  };

  handler_(snapshot.dump() + '\n');
}

}
