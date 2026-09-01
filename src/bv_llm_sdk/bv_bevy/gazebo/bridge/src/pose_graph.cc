#include "bv_sim_bridge/pose_graph.hh"

#include <optional>
#include <string_view>
#include <unordered_set>

namespace bv::sim
{
namespace
{

struct PoseNode
{
  std::string parent;
  gz::math::Pose3d local;
};

using PoseNodes = std::unordered_map<std::string, PoseNode>;

std::string ParentFrame(const gz::msgs::Pose &pose)
{
  if (!pose.has_header())
  {
    return {};
  }

  for (const auto &entry : pose.header().data())
  {
    if (entry.key() == "frame_id" && entry.value_size() > 0)
    {
      return entry.value(0);
    }
  }

  return {};
}

gz::math::Pose3d ToPose3d(const gz::msgs::Pose &pose)
{
  return {
    {
      pose.position().x(),
      pose.position().y(),
      pose.position().z(),
    },
    {
      pose.orientation().w(),
      pose.orientation().x(),
      pose.orientation().y(),
      pose.orientation().z(),
    },
  };
}

std::optional<gz::math::Pose3d> ResolvePose(
  std::string_view name,
  const PoseNodes &nodes,
  WorldPoses &resolved,
  std::unordered_set<std::string> &path)
{
  const auto cached = resolved.find(std::string(name));
  if (cached != resolved.end())
  {
    return cached->second;
  }

  const auto node = nodes.find(std::string(name));
  if (node == nodes.end() || !path.insert(node->first).second)
  {
    return std::nullopt;
  }

  auto world = node->second.local;
  if (nodes.find(node->second.parent) != nodes.end())
  {
    const auto parent = ResolvePose(node->second.parent, nodes, resolved, path);
    if (!parent)
    {
      path.erase(node->first);
      return std::nullopt;
    }
    world = *parent * world;
  }

  path.erase(node->first);
  resolved.emplace(node->first, world);
  return world;
}

}

WorldPoses ResolveWorldPoses(const gz::msgs::Pose_V &message)
{
  PoseNodes nodes;
  for (const auto &pose : message.pose())
  {
    nodes.insert_or_assign(
      pose.name(),
      PoseNode{ParentFrame(pose), ToPose3d(pose)});
  }

  WorldPoses resolved;
  std::unordered_set<std::string> path;
  for (const auto &entry : nodes)
  {
    ResolvePose(entry.first, nodes, resolved, path);
  }

  return resolved;
}

}
