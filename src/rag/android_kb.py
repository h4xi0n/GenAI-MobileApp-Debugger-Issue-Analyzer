"""Android SDK Knowledge Base and Canonical Architecture Repository.

Defines canonical failure modes, Compose state delegation traps, Coroutine ANRs,
lifecycle synchronization bugs, and memory leaks with verified remediation snippets.

Author: Arun Krishnan
"""

from enum import Enum

from src.core.models import KnowledgeCard


class KnowledgeCategory(str, Enum):
    """Categorization taxonomy for Android SDK knowledge documents.

    Attributes:
        COMPOSE: Jetpack Compose state delegation, recomposition loops, and WindowInsets.
        COROUTINES_ANR: Main thread blocking, dispatchers misuse, and looper deadlocks.
        LIFECYCLE: Activity/Fragment lifecycle traps, onSaveInstanceState timing.
        MEDIA3_CAMERAX: ExoPlayer/Media3 surface races and CameraX buffer leaks.
        MEMORY_LEAKS: ViewBinding retention, static references, and OOM issues.
        NETWORK_SECURITY: Cleartext traffic, TLS misconfigurations, and certificate pinning.
    """
    COMPOSE = "COMPOSE"
    COROUTINES_ANR = "COROUTINES_ANR"
    LIFECYCLE = "LIFECYCLE"
    MEDIA3_CAMERAX = "MEDIA3_CAMERAX"
    MEMORY_LEAKS = "MEMORY_LEAKS"
    NETWORK_SECURITY = "NETWORK_SECURITY"


CANONICAL_KNOWLEDGE_DOCUMENTS: list[dict] = [
    {
        "doc_id": "KB-COMPOSE-001",
        "category": KnowledgeCategory.COMPOSE.value,
        "title": "Jetpack Compose State Delegation & Recomposition Traps",
        "exception_patterns": [
            "java.lang.NullPointerException",
            "IllegalStateException",
            "RecompositionLoopException",
            "reading a state that was created after the snapshot",
        ],
        "symptoms": (
            "Screen flicker, infinite recomposition loops, sudden frame drops, or fatal "
            "NullPointerException when accessing state before asynchronous initialization completes."
        ),
        "root_cause_explanation": (
            "Declaring mutableStateOf(...) without wrapping it in remember or rememberSaveable causes "
            "the state to be re-initialized on every recomposition pass. Additionally, collecting Kotlin "
            "Flows with collectAsState() instead of collectAsStateWithLifecycle() causes flow collection "
            "to continue when the app is in the background, leading to unnecessary computation and "
            "unexpected state emissions."
        ),
        "recommended_fix_pattern": (
            "Always wrap local state variables in remember or rememberSaveable. Collect state in Jetpack Compose "
            "using androidx.lifecycle.compose.collectAsStateWithLifecycle() to ensure state collection is "
            "lifecycle-aware and pauses when the UI is not visible. Mark domain models with @Immutable or "
            "@Stable to prevent unnecessary recomposition."
        ),
        "code_example_kotlin": """@Composable
fun FeedScreen(
    viewModel: FeedViewModel = viewModel(),
    modifier: Modifier = Modifier
) {
    // Correct lifecycle-aware state collection
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    when (val state = uiState) {
        is FeedUiState.Loading -> LoadingIndicator(modifier)
        is FeedUiState.Success -> {
            // Null-safe access to validated feed items
            FeedContent(items = state.feedItems, modifier = modifier)
        }
        is FeedUiState.Error -> ErrorMessage(message = state.userMessage, modifier = modifier)
    }
}""",
        "doc_references": [
            "https://developer.android.com/develop/ui/compose/state",
            "https://developer.android.com/develop/ui/compose/lifecycle",
        ],
        "doc_url": "https://developer.android.com/develop/ui/compose/state",
    },
    {
        "doc_id": "KB-COROUTINE-001",
        "category": KnowledgeCategory.COROUTINES_ANR.value,
        "title": "Kotlin Coroutines Main Thread Contention & ANR Traps",
        "exception_patterns": [
            "android.os.ANRError",
            "ANR in",
            "Input dispatching timed out",
            "TimeoutException",
            "runBlocking",
        ],
        "symptoms": (
            "System ANR dialog ('Application Not Responding'), input dispatching timed out after 5000ms, "
            "UI touch events completely frozen, dropped looper frames."
        ),
        "root_cause_explanation": (
            "Invoking blocking synchronous I/O operations (Room DB, disk writes, JSON parsing, or network requests) "
            "or calling runBlocking directly on Dispatchers.Main blocks the Android Main Looper message queue. "
            "This starves the Chroreographer and WindowManager input dispatchers, triggering an ANR within 5 seconds."
        ),
        "recommended_fix_pattern": (
            "Offload all blocking execution and heavy computation to Dispatchers.IO or Dispatchers.Default "
            "using withContext. Never execute runBlocking on the UI thread. Use viewModelScope.launch with structured "
            "concurrency and attach a CoroutineExceptionHandler to manage unexpected child failures gracefully."
        ),
        "code_example_kotlin": """class Repository(private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO) {
    suspend fun loadUserData(userId: String): UserProfile = withContext(ioDispatcher) {
        // Safe off-main-thread I/O operation
        database.userDao().queryUserProfile(userId)
    }
}

// In ViewModel:
fun refreshProfile(userId: String) {
    viewModelScope.launch(coroutineExceptionHandler) {
        _uiState.value = UiState.Loading
        val profile = repository.loadUserData(userId)
        _uiState.value = UiState.Success(profile)
    }
}""",
        "doc_references": [
            "https://developer.android.com/kotlin/coroutines/coroutines-adv",
            "https://developer.android.com/topic/performance/vitals/anr",
        ],
        "doc_url": "https://developer.android.com/kotlin/coroutines/coroutines-adv",
    },
    {
        "doc_id": "KB-LIFECYCLE-001",
        "category": KnowledgeCategory.LIFECYCLE.value,
        "title": "Android Lifecycle & Fragment Transaction Timing Traps",
        "exception_patterns": [
            "java.lang.IllegalStateException: Can not perform this action after onSaveInstanceState",
            "IllegalStateException: FragmentManager has been destroyed",
            "commitStateLoss",
        ],
        "symptoms": (
            "Fatal crash: java.lang.IllegalStateException: Can not perform this action after onSaveInstanceState "
            "when navigating or replacing fragments during asynchronous network callbacks while the app is backgrounding."
        ),
        "root_cause_explanation": (
            "Executing FragmentTransaction.commit() after Activity.onSaveInstanceState() has been called causes "
            "state loss because the Android OS has already saved the fragment backstack state. The framework "
            "strictly throws IllegalStateException to prevent the application from losing user navigation state."
        ),
        "recommended_fix_pattern": (
            "Execute UI navigation and Fragment transactions only when the lifecycle is at least STARTED or RESUMED. "
            "Use viewLifecycleOwner.lifecycleScope.launch with repeatOnLifecycle(Lifecycle.State.RESUMED) or "
            "rely on the Jetpack Navigation component which coordinates transactions with lifecycle states automatically. "
            "If transaction state loss is explicitly acceptable, use commitAllowingStateLoss()."
        ),
        "code_example_kotlin": """viewLifecycleOwner.lifecycleScope.launch {
    viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
        viewModel.navigationEvents.collect { destination ->
            findNavController().navigate(destination)
        }
    }
}""",
        "doc_references": [
            "https://developer.android.com/guide/fragments/fragment-lifecycle",
            "https://developer.android.com/topic/libraries/architecture/lifecycle",
        ],
        "doc_url": "https://developer.android.com/guide/fragments/fragment-lifecycle",
    },
    {
        "doc_id": "KB-LEAK-001",
        "category": KnowledgeCategory.MEMORY_LEAKS.value,
        "title": "ViewBinding & Listener Memory Leaks in Lifecycle Owners",
        "exception_patterns": [
            "java.lang.OutOfMemoryError",
            "Activity has leaked",
            "OutOfMemoryError: Failed to allocate",
            "LeakCanary",
        ],
        "symptoms": (
            "Gradual memory growth across navigation cycles, frequent GC pauses, stuttering animations, "
            "and eventual OutOfMemoryError crashes."
        ),
        "root_cause_explanation": (
            "In Android Fragments, the Fragment instance often outlives its View hierarchy when added to the back stack. "
            "Holding a strong reference to the ViewBinding object past onDestroyView() keeps the entire View tree, "
            "its context, and associated drawables in memory, causing severe memory leaks."
        ),
        "recommended_fix_pattern": (
            "Always null out the backing ViewBinding reference in Fragment.onDestroyView(). Alternatively, "
            "use a LifecycleObserver-backed property delegate that automatically clears the binding reference "
            "when the Fragment's viewLifecycleOwner reaches ON_DESTROY."
        ),
        "code_example_kotlin": """class ProfileFragment : Fragment(R.layout.fragment_profile) {
    private var _binding: FragmentProfileBinding? = null
    private val binding get() = _binding!!

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        _binding = FragmentProfileBinding.bind(view)
        setupViews()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        // Crucial: release binding to prevent view leak
        _binding = null
    }
}""",
        "doc_references": [
            "https://developer.android.com/topic/libraries/view-binding#fragments",
            "https://developer.android.com/topic/performance/memory-overview",
        ],
        "doc_url": "https://developer.android.com/topic/libraries/view-binding#fragments",
    },
    {
        "doc_id": "KB-MEDIA3-001",
        "category": KnowledgeCategory.MEDIA3_CAMERAX.value,
        "title": "Media3 / ExoPlayer Playback Stalls & Surface Lifecycle Races",
        "exception_patterns": [
            "androidx.media3.exoplayer.ExoPlaybackException",
            "PlaybackException: ERROR_CODE_BEHIND_LIVE_WINDOW",
            "SurfaceTexture destroyed",
            "MediaCodec.CodecException",
        ],
        "symptoms": (
            "Audio continues playing but video freezes with a black screen; ERROR_CODE_BEHIND_LIVE_WINDOW "
            "on resume from lock screen; surface destruction crash during multi-window resizing."
        ),
        "root_cause_explanation": (
            "Races between the Android Activity surface lifecycle and Media3/ExoPlayer renderers. "
            "When an activity is paused or backgrounded, PlayerView's surface may be destroyed before the player "
            "is halted. For live streams, pausing playback causes the client buffer to drift behind the live server window."
        ),
        "recommended_fix_pattern": (
            "Release or detach player surfaces in onStop() (API >= 24) or onPause() (API < 24). Attach player "
            "error listeners that intercept ERROR_CODE_BEHIND_LIVE_WINDOW by seeking to default position and re-preparing. "
            "Use PlayerView with SurfaceView or TextureView suited for window transitions."
        ),
        "code_example_kotlin": """player.addListener(object : Player.Listener {
    override fun onPlayerError(error: PlaybackException) {
        if (error.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW) {
            // Recover live stream alignment
            player.seekToDefaultPosition()
            player.prepare()
        } else {
            handleFatalPlayerError(error)
        }
    }
})

override fun onStop() {
    super.onStop()
    playerView.player = null
    player.release()
}""",
        "doc_references": [
            "https://developer.android.com/media/media3/exoplayer",
            "https://developer.android.com/media/media3/exoplayer/live-streaming",
        ],
        "doc_url": "https://developer.android.com/media/media3/exoplayer",
    },
    {
        "doc_id": "KB-CAMERAX-001",
        "category": KnowledgeCategory.MEDIA3_CAMERAX.value,
        "title": "CameraX ProcessCameraProvider Surface Binding & Rotation Traps",
        "exception_patterns": [
            "java.lang.IllegalArgumentException: No available camera",
            "CameraAccessException",
            "ImageProxy not closed",
            "Pipeline frozen",
        ],
        "symptoms": (
            "Camera preview freezes after processing the first frame; ImageAnalysis stops receiving buffers; "
            "IllegalStateException when re-opening camera screen."
        ),
        "root_cause_explanation": (
            "In CameraX ImageAnalysis.Analyzer, failing to invoke imageProxy.close() prevents the camera pipeline "
            "from returning the buffer to the native buffer queue, stalling all subsequent frames. "
            "Additionally, attempting to bind use cases without unbinding previous ones leads to resource conflicts."
        ),
        "recommended_fix_pattern": (
            "Always wrap ImageAnalysis processing inside imageProxy.use { ... } to guarantee that the native buffer "
            "is closed after analysis. Call cameraProvider.unbindAll() prior to binding new use cases to a lifecycle owner."
        ),
        "code_example_kotlin": """val imageAnalysis = ImageAnalysis.Builder()
    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
    .build()

imageAnalysis.setAnalyzer(cameraExecutor) { imageProxy ->
    imageProxy.use { proxy ->
        // Process camera frame deterministically
        val bitmap = proxy.toBitmap()
        analyzeFrame(bitmap)
    } // Automatically calls proxy.close()
}

// Binding to lifecycle:
cameraProvider.unbindAll()
cameraProvider.bindToLifecycle(viewLifecycleOwner, cameraSelector, preview, imageAnalysis)""",
        "doc_references": [
            "https://developer.android.com/training/camerax/analyze",
            "https://developer.android.com/training/camerax/preview",
        ],
        "doc_url": "https://developer.android.com/training/camerax/analyze",
    },
    {
        "doc_id": "KB-COMPOSE-002",
        "category": KnowledgeCategory.COMPOSE.value,
        "title": "Jetpack Compose Missing WindowInsets & Scaffold Overlaps",
        "exception_patterns": [
            "WindowInsets",
            "Scaffold padding not consumed",
            "IME overlap",
            "Status bar overlap",
        ],
        "symptoms": (
            "Interactive buttons and text clipped behind system status bar, bottom navigation bar, or virtual keyboard; "
            "Scaffold content not honoring edge-to-edge window insets."
        ),
        "root_cause_explanation": (
            "When using enableEdgeToEdge(), the application content renders behind system bars. "
            "If the innerPadding provided by Compose Scaffold is ignored or not passed to inner layout modifiers, "
            "content collides with the system UI."
        ),
        "recommended_fix_pattern": (
            "Always apply Modifier.padding(innerPadding) to the top-level container within a Scaffold. "
            "Utilize WindowInsets.safeDrawing, WindowInsets.ime, or Modifier.windowInsetsPadding() for custom insets."
        ),
        "code_example_kotlin": """Scaffold(
    modifier = Modifier.fillMaxSize(),
    topBar = { TopAppBar(title = { Text("Feed") }) },
    contentWindowInsets = WindowInsets.safeDrawing
) { innerPadding ->
    // Crucial: apply innerPadding to prevent drawing under system bars
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(innerPadding)
    ) {
        items(feedList) { item -> FeedRow(item) }
    }
}""",
        "doc_references": [
            "https://developer.android.com/develop/ui/compose/layouts/insets",
            "https://developer.android.com/develop/ui/views/layout/edge-to-edge",
        ],
        "doc_url": "https://developer.android.com/develop/ui/compose/layouts/insets",
    },
]


def get_canonical_knowledge_base() -> list[KnowledgeCard]:
    """Instantiates and returns the canonical list of curated Android KnowledgeCards.

    Constructs validated Pydantic v2 KnowledgeCard models from the canonical document
    repository, mapping Kotlin remediation snippets and initializing confidence scores.

    Returns:
        List of initialized KnowledgeCard models ready for BM25 and vector indexing.
    """
    cards: list[KnowledgeCard] = []
    for doc in CANONICAL_KNOWLEDGE_DOCUMENTS:
        card = KnowledgeCard(
            doc_id=doc["doc_id"],
            category=doc["category"],
            title=doc["title"],
            exception_patterns=doc.get("exception_patterns", []),
            symptoms=doc["symptoms"],
            root_cause_explanation=doc["root_cause_explanation"],
            recommended_fix_pattern=doc["recommended_fix_pattern"],
            code_example_kotlin=doc["code_example_kotlin"],
            doc_references=doc.get("doc_references", []),
            doc_url=doc.get("doc_url"),
            remediation_snippet=doc["code_example_kotlin"],
            confidence_score=0.0,
            match_type="hybrid",
        )
        cards.append(card)
    return cards
