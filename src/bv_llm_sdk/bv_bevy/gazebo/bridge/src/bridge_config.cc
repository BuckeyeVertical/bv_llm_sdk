#include "bv_sim_bridge/bridge_config.hh"

#include <fstream>
#include <stdexcept>
#include <unordered_set>
#include <utility>

#include <nlohmann/json.hpp>

namespace bv::sim
{

BridgeConfig BridgeConfig::Load(const std::string &path)
{
  std::ifstream input(path);
  if (!input)
  {
    throw std::runtime_error("cannot open bridge config: " + path);
  }

  const auto json = nlohmann::json::parse(input);
  BridgeConfig config{
    json.at("pose_topic").get<std::string>(),
    json.at("listen_address").get<std::string>(),
    json.at("listen_port").get<std::uint16_t>(),
    {},
  };

  if (config.poseTopic.empty() || config.listenAddress.empty())
  {
    throw std::runtime_error("pose topic and listen address must not be empty");
  }

  std::unordered_set<std::string> gazeboNames;
  std::unordered_set<std::string> ids;

  for (const auto &entity : json.at("entities"))
  {
    EntityMapping mapping{
      entity.at("gazebo_name").get<std::string>(),
      entity.at("id").get<std::string>(),
      entity.at("kind").get<std::string>(),
    };

    if (mapping.gazeboName.empty() || mapping.id.empty() || mapping.kind.empty())
    {
      throw std::runtime_error("entity mapping fields must not be empty");
    }
    if (!gazeboNames.insert(mapping.gazeboName).second)
    {
      throw std::runtime_error("duplicate Gazebo entity: " + mapping.gazeboName);
    }
    if (!ids.insert(mapping.id).second)
    {
      throw std::runtime_error("duplicate protocol entity: " + mapping.id);
    }

    config.entities.push_back(std::move(mapping));
  }

  if (config.entities.empty())
  {
    throw std::runtime_error("at least one entity mapping is required");
  }

  return config;
}

}
