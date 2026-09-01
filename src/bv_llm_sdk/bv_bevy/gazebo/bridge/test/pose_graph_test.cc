#include "bv_sim_bridge/pose_graph.hh"

#include <cmath>
#include <stdexcept>
#include <string>

namespace
{

constexpr double kTolerance = 1e-9;

gz::msgs::Pose *AddPose(
  gz::msgs::Pose_V &message,
  const std::string &name,
  const std::string &parent,
  double x,
  double y,
  double z,
  double yaw)
{
  auto *pose = message.add_pose();
  pose->set_name(name);
  pose->mutable_position()->set_x(x);
  pose->mutable_position()->set_y(y);
  pose->mutable_position()->set_z(z);
  pose->mutable_orientation()->set_z(std::sin(yaw / 2.0));
  pose->mutable_orientation()->set_w(std::cos(yaw / 2.0));

  auto *frame = pose->mutable_header()->add_data();
  frame->set_key("frame_id");
  frame->add_value(parent);
  return pose;
}

void ExpectNear(double actual, double expected)
{
  if (std::abs(actual - expected) >= kTolerance)
  {
    throw std::runtime_error("resolved pose differs from expected pose");
  }
}

void ResolvesNestedPoses()
{
  gz::msgs::Pose_V message;
  AddPose(message, "vehicle", "world", 1.0, 2.0, 3.0, 1.5707963267948966);
  AddPose(message, "vehicle::camera", "vehicle", 2.0, 0.0, -0.5, 0.0);
  AddPose(
    message,
    "vehicle::camera::sensor",
    "vehicle::camera",
    0.0,
    0.0,
    0.0,
    0.0);

  const auto poses = bv::sim::ResolveWorldPoses(message);
  const auto &camera = poses.at("vehicle::camera::sensor");
  ExpectNear(camera.Pos().X(), 1.0);
  ExpectNear(camera.Pos().Y(), 4.0);
  ExpectNear(camera.Pos().Z(), 2.5);
  ExpectNear(camera.Rot().Yaw(), 1.5707963267948966);
}

void RejectsCycles()
{
  gz::msgs::Pose_V message;
  AddPose(message, "a", "b", 0.0, 0.0, 0.0, 0.0);
  AddPose(message, "b", "a", 0.0, 0.0, 0.0, 0.0);

  const auto poses = bv::sim::ResolveWorldPoses(message);
  if (!poses.empty())
  {
    throw std::runtime_error("cyclic pose graph was accepted");
  }
}

}

int main()
{
  ResolvesNestedPoses();
  RejectsCycles();
}
