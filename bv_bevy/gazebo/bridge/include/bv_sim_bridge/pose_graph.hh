#pragma once

#include <string>
#include <unordered_map>

#include <gz/math/Pose3.hh>
#include <gz/msgs/pose_v.pb.h>

namespace bv::sim
{

using WorldPoses = std::unordered_map<std::string, gz::math::Pose3d>;

WorldPoses ResolveWorldPoses(const gz::msgs::Pose_V &message);

}
