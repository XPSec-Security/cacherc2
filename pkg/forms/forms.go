package forms

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	FormsHost = "https://forms.cloud.microsoft"
	UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

type FormIdentity struct {
	TenantID string
	OwnerID  string
	FormID   string
	PageURL  string
	BaseURL  string
}

type FormDefinition struct {
	ID        string      `json:"id"`
	Title     string      `json:"title"`
	Questions []Question  `json:"questions"`
}

type Question struct {
	ID       string `json:"id"`
	Title    string `json:"title"`
	Order    int    `json:"order"`
	Required bool   `json:"required"`
	Type     string `json:"type"`
}

type Response struct {
	ID         int    `json:"id"`
	CreateDate string `json:"createDate"`
}

func ResolveFormIdentity(formID string) (*FormIdentity, error) {
	padded := strings.ReplaceAll(formID, "-", "+")
	padded = strings.ReplaceAll(padded, "_", "/")

	needed := (4 - len(padded)%4) % 4
	padded += strings.Repeat("=", needed)

	decoded, err := base64.StdEncoding.DecodeString(padded)
	if err != nil {
		return nil, fmt.Errorf("invalid form id: %v", err)
	}

	if len(decoded) <= 32 {
		return nil, fmt.Errorf("form id too short")
	}

	pageURL := FormsHost + "/Pages/ResponsePage.aspx?id=" + url.QueryEscape(formID)

	client := &http.Client{
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return nil
		},
	}
	req, _ := http.NewRequest("GET", pageURL, nil)
	req.Header.Set("User-Agent", UserAgent)

	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	io.ReadAll(resp.Body)

	finalURL := resp.Request.URL.String()
	u, _ := url.Parse(finalURL)

	tenantID := bytesToUUID(decoded[0:16])
	ownerID := bytesToUUID(decoded[16:32])

	return &FormIdentity{
		TenantID: tenantID,
		OwnerID:  ownerID,
		FormID:   formID,
		PageURL:  finalURL,
		BaseURL:  u.Scheme + "://" + u.Host,
	}, nil
}

func GetFormDefinition(form *FormIdentity) (*FormDefinition, error) {
	apiURL := fmt.Sprintf(
		"%s/formapi/api/%s/users/%s/light/runtimeForms('%s')?$expand=questions($expand=choices)",
		form.BaseURL, form.TenantID, form.OwnerID, form.FormID,
	)

	req, _ := http.NewRequest("GET", apiURL, nil)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", UserAgent)
	req.Header.Set("Referer", form.PageURL)
	req.Header.Set("Origin", form.BaseURL)

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var def FormDefinition
	json.NewDecoder(resp.Body).Decode(&def)
	return &def, nil
}

func SubmitResponse(form *FormIdentity, answers []map[string]string) error {
	apiURL := fmt.Sprintf(
		"%s/formapi/api/%s/users/%s/forms('%s')/responses",
		form.BaseURL, form.TenantID, form.OwnerID, form.FormID,
	)

	answersJSON, _ := json.Marshal(answers)

	payload := map[string]interface{}{
		"startDate":      time.Now().UTC().Format("2006-01-02T15:04:05.000Z"),
		"submitDate":     time.Now().UTC().Format("2006-01-02T15:04:05.000Z"),
		"answers":        string(answersJSON),
		"submitLanguage": "en-US",
	}

	body, _ := json.Marshal(payload)

	req, _ := http.NewRequest("POST", apiURL, bytes.NewBuffer(body))
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json;odata.metadata=minimal;odata.streaming=true")
	req.Header.Set("User-Agent", UserAgent)
	req.Header.Set("Referer", form.PageURL)
	req.Header.Set("Origin", form.BaseURL)

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 && resp.StatusCode != 201 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("http %d: %s", resp.StatusCode, string(body))
	}

	return nil
}

func bytesToUUID(b []byte) string {
	if len(b) < 16 {
		return ""
	}

	return fmt.Sprintf(
		"%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
		b[3], b[2], b[1], b[0],
		b[5], b[4],
		b[7], b[6],
		b[8], b[9],
		b[10], b[11], b[12], b[13], b[14], b[15],
	)
}
