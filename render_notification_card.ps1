param([Parameter(Mandatory=$true)][string]$InputJson,[Parameter(Mandatory=$true)][string]$OutputPng)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$card=[IO.File]::ReadAllText($InputJson,[Text.Encoding]::UTF8)|ConvertFrom-Json
$rows=@($card.items)
$rowHeight=if(@($rows|Where-Object {$_.detail}).Count -gt 0){194}else{156}
$height=300+[Math]::Max(1,$rows.Count)*$rowHeight
$bitmap=New-Object Drawing.Bitmap(1080,$height)
$g=[Drawing.Graphics]::FromImage($bitmap)
$g.SmoothingMode='AntiAlias'
$g.TextRenderingHint='AntiAliasGridFit'
$owned=New-Object 'System.Collections.Generic.List[System.IDisposable]'
function Brush([string]$hex){$b=New-Object Drawing.SolidBrush([Drawing.ColorTranslator]::FromHtml($hex));$owned.Add($b);return $b}
function Font([single]$size,[bool]$bold=$false){$style=if($bold){[Drawing.FontStyle]::Bold}else{[Drawing.FontStyle]::Regular};$f=New-Object Drawing.Font('Microsoft YaHei UI',$size,$style,[Drawing.GraphicsUnit]::Pixel);$owned.Add($f);return $f}
function Text([string]$text,[single]$size,[single]$x,[single]$y,[string]$color,[bool]$bold=$false){
    $g.DrawString($text,(Font $size $bold),(Brush $color),$x,$y)
}
try{
    $g.Clear([Drawing.ColorTranslator]::FromHtml('#F3F5F8'))
    $g.FillRectangle((Brush '#132B43'),0,0,1080,184)
    Text $card.labels.title 53 54 32 '#FFFFFF' $true
    Text ([string]$card.day) 26 58 116 '#C2D0DE'
    if($card.pages -gt 1){Text ("{0} / {1}" -f $card.page,$card.pages) 23 900 121 '#C2D0DE'}
    $y=212
    if($rows.Count -eq 0){
        Text $card.labels.empty 40 58 235 '#20364B' $true
        Text $card.labels.empty_detail 25 60 300 '#6C7B8A'
    }else{
        foreach($row in $rows){
            $g.FillRectangle((Brush '#FFFFFF'),40,$y,1000,($rowHeight-20))
            $kind=if($row.kind -eq 'BOND'){$card.labels.bond}else{$card.labels.stock}
            $accent=if($row.kind -eq 'BOND'){'#B56B24'}else{'#356DA5'}
            Text $kind 23 64 ($y+17) $accent $true
            $name=[string]$row.name
            $font=Font 38 $true
            $rect=New-Object Drawing.RectangleF(62,($y+56),590,58)
            $format=New-Object Drawing.StringFormat
            $owned.Add($format)
            $format.Trimming=[Drawing.StringTrimming]::EllipsisCharacter
            $format.FormatFlags=[Drawing.StringFormatFlags]::NoWrap
            $g.DrawString($name,$font,(Brush '#20364B'),$rect,$format)
            $color=[string]$row.status_color
            $g.FillEllipse((Brush $color),672,($y+54),10,10)
            $statusRect=New-Object Drawing.RectangleF(696,($y+39),328,65)
            $g.DrawString([string]$row.status_label,(Font 27 $true),(Brush $color),$statusRect,$format)
            if($row.detail){
                $detailRect=New-Object Drawing.RectangleF(64,($y+121),940,40)
                $g.DrawString([string]$row.detail,(Font 22),(Brush '#6C7B8A'),$detailRect,$format)
            }
            $y+=$rowHeight
        }
    }
    Text $card.labels.footer 22 58 ($height-59) '#81909F'
    $bitmap.Save($OutputPng,[Drawing.Imaging.ImageFormat]::Png)
}finally{
    $g.Dispose();$bitmap.Dispose()
    foreach($item in $owned){$item.Dispose()}
}
