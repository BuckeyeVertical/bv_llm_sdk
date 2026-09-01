#pragma once

#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace bv::sim
{

class TcpStateServer
{
public:
  TcpStateServer(std::string address, std::uint16_t port);
  ~TcpStateServer();

  TcpStateServer(const TcpStateServer &) = delete;
  TcpStateServer &operator=(const TcpStateServer &) = delete;

  std::uint16_t Port() const;
  void Publish(std::string snapshot);

private:
  struct Client
  {
    int socket;
    std::uint64_t sentGeneration;
  };

  void AcceptClients();
  void Broadcast();
  int AcceptClient();

  std::string address_;
  std::uint16_t port_;
  int listener_{-1};
  bool stopping_{false};
  std::string latest_;
  std::uint64_t generation_{0};
  std::vector<Client> clients_;
  std::mutex mutex_;
  std::condition_variable changed_;
  std::thread acceptWorker_;
  std::thread broadcastWorker_;
};

}
