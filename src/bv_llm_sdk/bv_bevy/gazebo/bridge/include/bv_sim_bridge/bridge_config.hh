#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace bv::sim
{

struct EntityMapping
{
  std::string gazeboName;
  std::string id;
  std::string kind;
};

struct BridgeConfig
{
  std::string poseTopic;
  std::string listenAddress;
  std::uint16_t listenPort;
  std::vector<EntityMapping> entities;

  static BridgeConfig Load(const std::string &path);
};

}
