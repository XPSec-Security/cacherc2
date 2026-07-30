package sysinfo

import (
	"fmt"
	"net"
	"os"
	"os/user"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

type EnvironmentInfo struct {
	Username       string
	Hostname       string
	Domain         string
	IsDomainJoined bool
	LocalIP        string
	UUID           string
	DateTime       time.Time
	DateTimeString string
}

var (
	runUUID     string
	runUUIDOnce sync.Once
)

func getRunUUID() string {
	runUUIDOnce.Do(func() {
		runUUID = uuid.New().String()[:8]
	})
	return runUUID
}

func GetEnvironmentInfo() EnvironmentInfo {
	username := "unknown"
	if u, err := user.Current(); err == nil {
		username = u.Username
		if idx := strings.LastIndex(username, `\`); idx >= 0 {
			username = username[idx+1:]
		}
	}

	hostname, _ := os.Hostname()

	domain := os.Getenv("USERDNSDOMAIN")
	if domain == "" {
		domain = os.Getenv("USERDOMAIN")
	}
	isDomainJoined := domain != "" && !strings.EqualFold(domain, hostname)
	if domain == "" {
		domain = hostname
	}

	domainDisplay := domain
	if !isDomainJoined {
		domainDisplay = domain + " (workgroup/standalone)"
	}

	localIP := getLocalIP()
	if localIP == "" {
		localIP = "127.0.0.1"
	}

	utc3 := time.FixedZone("UTC-03:00", -3*3600)
	now := time.Now().In(utc3)

	return EnvironmentInfo{
		Username:       username,
		Hostname:       hostname,
		Domain:         domainDisplay,
		IsDomainJoined: isDomainJoined,
		LocalIP:        localIP,
		UUID:           getRunUUID(),
		DateTime:       now,
		DateTimeString: now.Format("2006-01-02 15:04:05"),
	}
}

func getLocalIP() string {
	interfaces, err := net.Interfaces()
	if err != nil {
		return ""
	}

	var fallbackIP, virtualIP string

	for _, iface := range interfaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}

		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}

		for _, addr := range addrs {
			ipNet, ok := addr.(*net.IPNet)
			if !ok {
				continue
			}

			ip := ipNet.IP.To4()
			if ip == nil {
				continue // ignores IPv6
			}

			if ip.IsLinkLocalUnicast() {
				if fallbackIP == "" {
					fallbackIP = ip.String()
				}
				continue
			}

			if ip.IsPrivate() {
				if isVirtualInterface(iface.Name) {
					if virtualIP == "" {
						virtualIP = ip.String()
					}
					continue
				}
				return ip.String()
			}

			if fallbackIP == "" {
				fallbackIP = ip.String()
			}
		}
	}

	if virtualIP != "" {
		return virtualIP
	}
	return fallbackIP
}

func isVirtualInterface(name string) bool {
	name = strings.ToLower(name)
	for _, needle := range []string{
		"vmware", "virtual", "vethernet", "veth",
		"hyper-v", "vbox", "virtualbox",
	} {
		if strings.Contains(name, needle) {
			return true
		}
	}
	return false
}

func (e EnvironmentInfo) String() string {
	return fmt.Sprintf(
		"User: %s | Host: %s | Domain: %s | IP: %s | UUID: %s",
		e.Username, e.Hostname, e.Domain, e.LocalIP, e.UUID,
	)
}