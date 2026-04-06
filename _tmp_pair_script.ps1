
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,System.Windows.Forms

function Write-JsonResult($ok, $reason, $selectedName) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    @{
        ok = $ok
        reason = $reason
        selectedName = $selectedName
    } | ConvertTo-Json -Compress
}

function Test-NameContains([string]$candidate, [string[]]$terms) {
    if ([string]::IsNullOrWhiteSpace($candidate)) { return $false }
    foreach ($term in $terms) {
        if ([string]::IsNullOrWhiteSpace($term)) { continue }
        if ($candidate.IndexOf($term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }
    return $false
}

function Find-WindowByContains([string[]]$terms) {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($window in $windows) {
        if (Test-NameContains $window.Current.Name $terms) {
            return $window
        }
    }
    return $null
}

function Find-ElementByNames($root, [string[]]$names, [string[]]$controlTypeNames, [bool]$containsMatch) {
    if ($null -eq $root) { return $null }
    $items = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($item in $items) {
        $name = [string]$item.Current.Name
        $typeName = [string]$item.Current.ControlType.ProgrammaticName
        if ($controlTypeNames.Count -gt 0 -and -not ($controlTypeNames -contains $typeName)) {
            continue
        }
        if ($containsMatch) {
            if (Test-NameContains $name $names) {
                return $item
            }
            continue
        }
        foreach ($expected in $names) {
            if ($name -eq $expected) {
                return $item
            }
        }
    }
    return $null
}

function Resolve-InvokableElement($item) {
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $current = $item
    for ($i = 0; $i -lt 6 -and $null -ne $current; $i++) {
        $typeName = [string]$current.Current.ControlType.ProgrammaticName
        if ($typeName -in @(
            'ControlType.Button',
            'ControlType.ListItem',
            'ControlType.Custom',
            'ControlType.Hyperlink'
        )) {
            return $current
        }
        $current = $walker.GetParent($current)
    }
    return $item
}

function Invoke-Element($item) {
    if ($null -eq $item) { return $false }
    $pattern = $null
    $invokePattern = [System.Windows.Automation.InvokePattern]::Pattern
    if ($item.TryGetCurrentPattern($invokePattern, [ref]$pattern)) {
        $pattern.Invoke()
        return $true
    }
    $pattern = $null
    $selectionPattern = [System.Windows.Automation.SelectionItemPattern]::Pattern
    if ($item.TryGetCurrentPattern($selectionPattern, [ref]$pattern)) {
        $pattern.Select()
        return $true
    }
    $legacyPatternType = [type]::GetType('System.Windows.Automation.LegacyIAccessiblePattern, UIAutomationClient')
    if ($null -ne $legacyPatternType) {
        $pattern = $null
        $legacyPattern = $legacyPatternType::Pattern
        if ($item.TryGetCurrentPattern($legacyPattern, [ref]$pattern)) {
            $pattern.DoDefaultAction()
            return $true
        }
    }
    try {
        $item.SetFocus()
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
        return $true
    } catch {
        return $false
    }
}

function Wait-Window([string[]]$terms, [double]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $window = Find-WindowByContains $terms
        if ($null -ne $window) {
            return $window
        }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

function Wait-Element($root, [string[]]$names, [string[]]$controlTypeNames, [bool]$containsMatch, [double]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $item = Find-ElementByNames $root $names $controlTypeNames $containsMatch
        if ($null -ne $item) {
            return $item
        }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

function Click-OptionalButtons($root, [string[]]$names) {
    $button = Find-ElementByNames $root $names @('ControlType.Button') $false
    if ($null -ne $button) {
        [void](Invoke-Element $button)
        return $true
    }
    return $false
}

try {
    $targetName = 'LOWA Mouse'
    $targetMac = 'D5:E7:15:41:4C:B3'
    $timeoutSec = [double]10.000

    Start-Process explorer.exe 'ms-settings:bluetooth'

    $settingsWindow = Wait-Window @('Bluetooth', '蓝牙', '设置', 'Settings') 12
    if ($null -eq $settingsWindow) {
        Write-Output (Write-JsonResult $false '未找到系统蓝牙设置窗口。' '')
        exit 0
    }

    $addButton = Wait-Element $settingsWindow @('Add device', '添加设备') @('ControlType.Button') $false 10
    if ($null -eq $addButton) {
        Write-Output (Write-JsonResult $false '未找到“添加设备”按钮。' '')
        exit 0
    }
    if (-not (Invoke-Element $addButton)) {
        Write-Output (Write-JsonResult $false '无法触发“添加设备”按钮。' '')
        exit 0
    }

    $dialog = Wait-Window @('Add a device', '添加设备') 1
    if ($null -eq $dialog) {
        $dialog = $settingsWindow
    }

    $bluetoothButton = Wait-Element $dialog @('Bluetooth', '蓝牙') @('ControlType.Button', 'ControlType.ListItem', 'ControlType.Custom', 'ControlType.Text') $true 6
    if ($null -eq $bluetoothButton) {
        $bluetoothButton = Wait-Element ([System.Windows.Automation.AutomationElement]::RootElement) @('Bluetooth', '蓝牙') @('ControlType.Button', 'ControlType.ListItem', 'ControlType.Custom', 'ControlType.Text') $true 4
    }
    if ($null -eq $bluetoothButton) {
        Write-Output (Write-JsonResult $false '未找到“蓝牙”配对入口。' '')
        exit 0
    }
    if (-not (Invoke-Element $bluetoothButton)) {
        Write-Output (Write-JsonResult $false '无法触发“蓝牙”配对入口。' '')
        exit 0
    }

    $targetTerms = @($targetName)
    if (-not [string]::IsNullOrWhiteSpace($targetMac)) {
        $targetTerms += ($targetMac -replace '[:\-\s]', '')
    }

    $deviceItem = Wait-Element $dialog $targetTerms @('ControlType.ListItem', 'ControlType.Button', 'ControlType.Custom', 'ControlType.Text') $true $timeoutSec
    if ($null -eq $deviceItem) {
        $deviceItem = Wait-Element ([System.Windows.Automation.AutomationElement]::RootElement) $targetTerms @('ControlType.ListItem', 'ControlType.Button', 'ControlType.Custom', 'ControlType.Text') $true 4
    }
    if ($null -eq $deviceItem) {
        Write-Output (Write-JsonResult $false '在“添加设备”界面中未找到目标蓝牙设备。' '')
        exit 0
    }

    $clickable = Resolve-InvokableElement $deviceItem
    $selectedName = [string]$clickable.Current.Name
    if (-not (Invoke-Element $clickable)) {
        Write-Output (Write-JsonResult $false '已找到目标设备，但无法触发配对。' $selectedName)
        exit 0
    }

    $buttonDeadline = (Get-Date).AddSeconds([Math]::Min(6.0, [Math]::Max(2.0, $timeoutSec / 3.0)))
    while ((Get-Date) -lt $buttonDeadline) {
        $clicked = $false
        foreach ($buttonNames in @(
            @('Pair', '配对'),
            @('Connect', '连接'),
            @('Done', '完成'),
            @('Close', '关闭')
        )) {
            if ((Click-OptionalButtons $dialog $buttonNames) -or (Click-OptionalButtons ([System.Windows.Automation.AutomationElement]::RootElement) $buttonNames)) {
                $clicked = $true
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $clicked) {
            Start-Sleep -Milliseconds 350
        }
    }

    Write-Output (Write-JsonResult $true '' $selectedName)
} catch {
    $message = $_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($message)) {
        $message = $_ | Out-String
    }
    Write-Output (Write-JsonResult $false $message '')
}
