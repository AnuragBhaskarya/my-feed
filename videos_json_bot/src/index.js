export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    
    try {
      if (url.pathname === '/debug') {
        return new Response(JSON.stringify({
          env_available: !!env,
          secrets_status: {
            dropbox_token: env.DROPBOX_REFRESH_TOKEN ? 'SET' : 'MISSING',
            dropbox_key: env.DROPBOX_APP_KEY ? 'SET' : 'MISSING',
            dropbox_secret: env.DROPBOX_APP_SECRET ? 'SET' : 'MISSING',
            github_token: env.GITHUB_TOKEN ? 'SET' : 'MISSING',
            github_owner: env.GITHUB_OWNER ? 'SET' : 'MISSING',
            github_repo: env.GITHUB_REPO ? 'SET' : 'MISSING'
          }
        }, null, 2), {
          headers: { 'Content-Type': 'application/json' }
        });
      }
      
      if (url.pathname === '/videos.json') {
        const videoData = await fetchFromGitHub(env);
        return new Response(JSON.stringify(videoData), {
          headers: {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
          }
        });
      }
      
      // Add this to your fetch handler in the main export default
      if (url.pathname === '/github-test-detailed') {
        try {
          // Test repository access
          const repoResponse = await fetch(`https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}`, {
            headers: {
              'Authorization': `token ${env.GITHUB_TOKEN}`,
              'Accept': 'application/vnd.github.v3+json',
              'User-Agent': 'CloudflareWorker-VideoBot/1.0'
            }
          });
          
          const repoInfo = repoResponse.ok ? await repoResponse.json() : null;
          
          // Test file access
          const fileResponse = await fetch(`https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/videos.json`, {
            headers: {
              'Authorization': `token ${env.GITHUB_TOKEN}`,
              'Accept': 'application/vnd.github.v3+json',
              'User-Agent': 'CloudflareWorker-VideoBot/1.0'
            }
          });
          
          return new Response(JSON.stringify({
            repository_access: {
              status: repoResponse.status,
              exists: repoResponse.ok,
              permissions: repoInfo ? {
                name: repoInfo.name,
                private: repoInfo.private,
                permissions: repoInfo.permissions
              } : 'No access'
            },
            file_access: {
              status: fileResponse.status,
              exists: fileResponse.status === 200,
              can_write: fileResponse.status !== 403
            },
            token_info: {
              github_owner: env.GITHUB_OWNER,
              github_repo: env.GITHUB_REPO,
              token_preview: env.GITHUB_TOKEN ? env.GITHUB_TOKEN.substring(0, 10) + '...' : 'MISSING'
            }
          }, null, 2), {
            headers: { 'Content-Type': 'application/json' }
          });
          
        } catch (error) {
          return new Response(JSON.stringify({
            error: error.message,
            github_owner: env.GITHUB_OWNER,
            github_repo: env.GITHUB_REPO
          }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' }
          });
        }
      }

      
      if (url.pathname === '/fetch') {
        const accessToken = await getAccessToken(env);
        const videoFiles = await listDropboxFiles("", accessToken);
        const videoList = videoFiles.map(file => toRawDropboxLink(file.url));
        
        await updateGitHubJSON(videoList, env);
        
        return new Response(JSON.stringify(videoList), {
          headers: {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
          }
        });
      }
      
      return new Response('Worker is running! Try /debug endpoint');
      
    } catch (error) {
      return new Response('Error: ' + error.message, { status: 500 });
    }
  },

  async scheduled(event, env, ctx) {
    try {
      const accessToken = await getAccessToken(env);
      const videoFiles = await listDropboxFiles("", accessToken);
      const videoList = videoFiles.map(file => toRawDropboxLink(file.url));
      
      await updateGitHubJSON(videoList, env);
      console.log(`✅ Updated ${videoList.length} videos to GitHub`);
    } catch (error) {
      console.error('❌ Cron job failed:', error.message);
    }
  }
};

// Helper functions
async function getAccessToken(env) {
  if (!env.DROPBOX_REFRESH_TOKEN || !env.DROPBOX_APP_KEY || !env.DROPBOX_APP_SECRET) {
    throw new Error('Missing Dropbox secrets');
  }
  
  const credentials = btoa(`${env.DROPBOX_APP_KEY}:${env.DROPBOX_APP_SECRET}`);
  
  const response = await fetch('https://api.dropbox.com/oauth2/token', {
    method: 'POST',
    headers: {
      'Authorization': `Basic ${credentials}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams({
      'grant_type': 'refresh_token',
      'refresh_token': env.DROPBOX_REFRESH_TOKEN
    })
  });
  
  if (!response.ok) {
    throw new Error(`Token refresh failed: ${response.status}`);
  }
  
  const tokenData = await response.json();
  return tokenData.access_token;
}

async function listDropboxFiles(path, token) {
  const listResponse = await fetch('https://api.dropboxapi.com/2/files/list_folder', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ path, recursive: false })
  });
  
  if (!listResponse.ok) {
    throw new Error(`Dropbox list failed: ${listResponse.status}`);
  }
  
  const listData = await listResponse.json();
  const files = [];
  
  for (const entry of listData.entries) {
    if (entry['.tag'] === 'file') {
      const sharedLink = await getSharedLink(entry.path_lower, token);
      files.push({ url: sharedLink });
    }
  }
  
  return files;
}

async function getSharedLink(path, token) {
  // Get existing or create new shared link
  const getLinksRes = await fetch('https://api.dropboxapi.com/2/sharing/list_shared_links', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ path, direct_only: true })
  });
  
  const linksData = await getLinksRes.json();
  
  if (linksData.links && linksData.links.length > 0) {
    return linksData.links[0].url;
  }
  
  // Create new link
  const createLinkRes = await fetch('https://api.dropboxapi.com/2/sharing/create_shared_link_with_settings', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ path })
  });
  
  const createData = await createLinkRes.json();
  return createData.url;
}

function toRawDropboxLink(url) {
  const [base, queryString] = url.split('?');
  if (!queryString) return url;
  const params = new URLSearchParams(queryString);
  params.delete('dl');
  params.set('raw', '1');
  return `${base}?${params.toString()}`;
}

// Replaces your current updateGitHubJSON
async function updateGitHubJSON(videoList, env) {
  // 0️⃣ sanity-check secrets
  if (!env.GITHUB_TOKEN || !env.GITHUB_OWNER || !env.GITHUB_REPO) {
    throw new Error('Missing GitHub secrets');
  }

  /*****************************************************************
   * 1️⃣  QUICK-EXIT if nothing changed
   *****************************************************************/
  try {
    // fetch the latest public copy; add cache-buster to bypass Fastly
    const rawUrl =
      `https://${env.GITHUB_OWNER}.github.io/${env.GITHUB_REPO}/videos.json?ts=${Date.now()}`;
    const resp = await fetch(rawUrl, { headers: { 'User-Agent': 'CF-Worker' } });

    if (resp.ok) {
      const current = await resp.json();
      if (JSON.stringify(current) === JSON.stringify(videoList)) {
        console.log('ℹ️  No change in video list — skipping GitHub commit');
        return { skipped: true };
      }
    }
  } catch (err) {
    // ignore parse/network errors → treat as “changed” and continue
    console.log('🔍 Could not compare with existing file, proceeding to commit');
  }

  /*****************************************************************
   * 2️⃣  Get SHA of existing file (needed only when we’ll commit)
   *****************************************************************/
  let currentSHA = null;
  try {
    const metaRes = await fetch(
      `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/videos.json`,
      {
        headers: {
          'Authorization': `token ${env.GITHUB_TOKEN}`,
          'Accept': 'application/vnd.github.v3+json',
          'User-Agent': 'CloudflareWorker-VideoBot/1.0'
        }
      }
    );

    if (metaRes.ok) {
      const meta = await metaRes.json();
      currentSHA = meta.sha;
      console.log('✅ Existing file SHA:', currentSHA.slice(0, 8));
    } else if (metaRes.status === 404) {
      console.log('📝 File does not exist yet — will create it');
    } else {
      console.warn('⚠️  Could not read file metadata:', metaRes.status);
    }
  } catch (e) {
    console.log('📄 SHA lookup failed, assuming new file:', e.message);
  }

  /*****************************************************************
   * 3️⃣  Commit new content
   *****************************************************************/
  const fileContent   = JSON.stringify(videoList, null, 2);
  const base64Content = btoa(unescape(encodeURIComponent(fileContent)));

  const payload = {
    message: `Update videos.json – ${new Date().toISOString()}`,
    content: base64Content,
    ...(currentSHA && { sha: currentSHA })
  };

  const commitRes = await fetch(
    `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/videos.json`,
    {
      method: 'PUT',
      headers: {
        'Authorization': `token ${env.GITHUB_TOKEN}`,
        'Content-Type': 'application/json',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'CloudflareWorker-VideoBot/1.0'
      },
      body: JSON.stringify(payload)
    }
  );

  if (!commitRes.ok) {
    const body = await commitRes.text();
    throw new Error(`GitHub update failed (${commitRes.status}): ${body}`);
  }

  const result = await commitRes.json();
  console.log('🚀 GitHub file updated:', result.commit.sha.slice(0, 8));
  return result;
}



async function fetchFromGitHub(env) {
  const publicUrl = `https://${env.GITHUB_OWNER}.github.io/${env.GITHUB_REPO}/videos.json`;
  
  const response = await fetch(publicUrl);
  
  if (!response.ok) {
    throw new Error(`GitHub fetch failed: ${response.status}`);
  }
  
  return await response.json();
}