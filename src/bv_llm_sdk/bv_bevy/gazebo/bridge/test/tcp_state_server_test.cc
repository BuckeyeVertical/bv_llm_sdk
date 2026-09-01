#include "bv_sim_bridge/tcp_state_server.hh"

#include <arpa/inet.h>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <unistd.h>

namespace
{

int Connect(std::uint16_t port)
{
  const int client = socket(AF_INET, SOCK_STREAM, 0);
  if (client < 0)
  {
    throw std::runtime_error("cannot create test socket");
  }

  sockaddr_in endpoint{};
  endpoint.sin_family = AF_INET;
  endpoint.sin_port = htons(port);
  inet_pton(AF_INET, "127.0.0.1", &endpoint.sin_addr);
  if (connect(client, reinterpret_cast<sockaddr *>(&endpoint), sizeof(endpoint)) < 0)
  {
    close(client);
    throw std::runtime_error("cannot connect test socket");
  }
  return client;
}

std::string ReadLine(int client)
{
  std::string line;
  while (line.empty() || line.back() != '\n')
  {
    pollfd descriptor{client, POLLIN, 0};
    if (poll(&descriptor, 1, 2'000) <= 0)
    {
      throw std::runtime_error("timed out reading test snapshot");
    }

    char value{};
    if (recv(client, &value, 1, 0) != 1)
    {
      throw std::runtime_error("test snapshot stream closed");
    }
    line.push_back(value);
  }
  return line;
}

}

int main()
{
  bv::sim::TcpStateServer server("127.0.0.1", 0);
  const int first = Connect(server.Port());
  const int second = Connect(server.Port());

  server.Publish("first\n");
  if (ReadLine(first) != "first\n" || ReadLine(second) != "first\n")
  {
    throw std::runtime_error("consumers received different snapshots");
  }

  shutdown(first, SHUT_RDWR);
  close(first);
  server.Publish("second\n");
  if (ReadLine(second) != "second\n")
  {
    throw std::runtime_error("remaining consumer stopped receiving snapshots");
  }

  shutdown(second, SHUT_RDWR);
  close(second);
}
