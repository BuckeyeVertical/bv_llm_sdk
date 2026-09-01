#include "bv_sim_bridge/tcp_state_server.hh"

#include <algorithm>
#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <utility>
#include <sys/socket.h>
#include <unistd.h>

namespace bv::sim
{
namespace
{

void SendAll(int socket, const std::string &payload)
{
  std::size_t sent = 0;

  while (sent < payload.size())
  {
    const auto result = send(
      socket,
      payload.data() + sent,
      payload.size() - sent,
      MSG_NOSIGNAL | MSG_DONTWAIT);

    if (result <= 0)
    {
      throw std::runtime_error(std::strerror(errno));
    }

    sent += static_cast<std::size_t>(result);
  }
}

}

TcpStateServer::TcpStateServer(std::string address, std::uint16_t port)
  : address_(std::move(address)), port_(port)
{
  listener_ = socket(AF_INET, SOCK_STREAM, 0);
  if (listener_ < 0)
  {
    throw std::runtime_error("cannot create TCP listener");
  }

  const int reuse = 1;
  setsockopt(listener_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

  sockaddr_in endpoint{};
  endpoint.sin_family = AF_INET;
  endpoint.sin_port = htons(port_);

  if (inet_pton(AF_INET, address_.c_str(), &endpoint.sin_addr) != 1)
  {
    close(listener_);
    throw std::runtime_error("listen address must be an IPv4 address");
  }
  if (bind(listener_, reinterpret_cast<sockaddr *>(&endpoint), sizeof(endpoint)) < 0)
  {
    close(listener_);
    throw std::runtime_error("cannot bind TCP listener: " +
                             std::string(std::strerror(errno)));
  }
  if (listen(listener_, 4) < 0)
  {
    close(listener_);
    throw std::runtime_error("cannot listen on TCP socket");
  }

  sockaddr_in bound{};
  socklen_t boundSize = sizeof(bound);
  if (getsockname(
        listener_, reinterpret_cast<sockaddr *>(&bound), &boundSize) < 0)
  {
    close(listener_);
    throw std::runtime_error("cannot read TCP listener address");
  }
  port_ = ntohs(bound.sin_port);

  acceptWorker_ = std::thread(&TcpStateServer::AcceptClients, this);
  broadcastWorker_ = std::thread(&TcpStateServer::Broadcast, this);
}

TcpStateServer::~TcpStateServer()
{
  {
    std::lock_guard lock(mutex_);
    stopping_ = true;
  }
  changed_.notify_all();
  shutdown(listener_, SHUT_RDWR);
  close(listener_);

  if (acceptWorker_.joinable())
  {
    acceptWorker_.join();
  }
  if (broadcastWorker_.joinable())
  {
    broadcastWorker_.join();
  }

  for (const auto &client : clients_)
  {
    close(client.socket);
  }
}

std::uint16_t TcpStateServer::Port() const
{
  return port_;
}

void TcpStateServer::Publish(std::string snapshot)
{
  {
    std::lock_guard lock(mutex_);
    latest_ = std::move(snapshot);
    ++generation_;
  }
  changed_.notify_one();
}

void TcpStateServer::AcceptClients()
{
  std::cout << "BV simulation state listening on " << address_ << ':' << port_
            << std::endl;

  while (true)
  {
    const int client = AcceptClient();
    if (client < 0)
    {
      return;
    }

    {
      std::lock_guard lock(mutex_);
      if (stopping_)
      {
        close(client);
        return;
      }
      clients_.push_back({client, 0});
    }
    changed_.notify_one();
    std::cout << "State consumer connected" << std::endl;
  }
}

void TcpStateServer::Broadcast()
{
  while (true)
  {
    std::string snapshot;
    std::uint64_t targetGeneration = 0;
    std::vector<int> recipients;

    {
      std::unique_lock lock(mutex_);
      changed_.wait(lock, [this] {
        return stopping_ || std::any_of(
          clients_.begin(), clients_.end(), [this](const Client &client) {
            return client.sentGeneration < generation_;
          });
      });

      if (stopping_)
      {
        return;
      }

      snapshot = latest_;
      targetGeneration = generation_;
      for (const auto &client : clients_)
      {
        if (client.sentGeneration < targetGeneration)
        {
          recipients.push_back(client.socket);
        }
      }
    }

    std::vector<int> disconnected;
    for (const int client : recipients)
    {
      try
      {
        SendAll(client, snapshot);
      }
      catch (const std::exception &error)
      {
        std::cout << "State consumer disconnected: " << error.what() << std::endl;
        disconnected.push_back(client);
      }
    }

    std::lock_guard lock(mutex_);
    for (auto &client : clients_)
    {
      const auto wasSent = std::find(
        recipients.begin(), recipients.end(), client.socket) != recipients.end();
      const auto failed = std::find(
        disconnected.begin(), disconnected.end(), client.socket) !=
        disconnected.end();
      if (wasSent && !failed)
      {
        client.sentGeneration = targetGeneration;
      }
    }
    clients_.erase(
      std::remove_if(
        clients_.begin(), clients_.end(), [&disconnected](const Client &client) {
          const auto removed = std::find(
            disconnected.begin(), disconnected.end(), client.socket) !=
            disconnected.end();
          if (removed)
          {
            close(client.socket);
          }
          return removed;
        }),
      clients_.end());
  }
}

int TcpStateServer::AcceptClient()
{
  while (true)
  {
    const int client = accept(listener_, nullptr, nullptr);
    if (client >= 0)
    {
      return client;
    }

    {
      std::lock_guard lock(mutex_);
      if (stopping_)
      {
        return -1;
      }
    }

    if (errno != EINTR)
    {
      std::cerr << "TCP accept failed: " << std::strerror(errno) << std::endl;
      return -1;
    }
  }
}

}
