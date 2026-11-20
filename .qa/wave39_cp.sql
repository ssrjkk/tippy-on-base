SELECT kind, left(counterparty,24) AS cp, count(*) FROM tx_log GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20;
SELECT 'has_angle_or_amp' AS probe, count(*) FROM tx_log WHERE counterparty ~ '[<>&]';
SELECT 'note_angle' AS probe, count(*) FROM tx_log WHERE note ~ '[<>&]';
