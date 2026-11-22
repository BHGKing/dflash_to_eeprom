<?php
$sha1 = $_GET["sha1"];

require("settings.php");

// Check user input. Shall be exactly 40 hexadecimal characters.
$result = preg_match('/^[a-f0-9]{40}$/', $sha1);
if(strlen($sha1) != 40 || !($result === 1)) {
    die("Error!");
}
else {
    $sharead = sha1_file($target_path . '/' . $sha1 . '.bin');
    if($sharead != $sha1) {
        die("Error!");
    }
    else {
        $eeprom = $result_path . '/' . $sha1 . '.bin';
        if (!is_file($eeprom)) {
            die("Error!");
        }
        header('Content-disposition: attachment; filename=eeprom.bin');
        header('Content-type: application/octet-stream');
        header('Content-Length: '.filesize($eeprom));
        header("Pragma: no-cache");
        header("Expires: 0");
        
        $fp = fopen($eeprom, "rb") or die("Unable to open file!");
        echo stream_get_contents($fp);
        fclose($fp);
    }
}
?>
